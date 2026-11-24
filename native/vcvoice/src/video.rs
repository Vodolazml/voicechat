//! Video: screen shares and cameras, both ways, in the engine instead of the app's Python.
//!
//! Our own LiveKit participant "u<id>" (the token the app's video connection used, so the server and older
//! apps see no difference) is opened only while there is video to watch or to send. Everything is
//! end-to-end encrypted with the same keys as the voice.
//!
//! - Watching: the stream is decrypted and decoded here; at most 30 pictures a second go to the window
//!   through shared memory (`shm`), the rest are dropped without a copy.
//! - Sharing: WebRTC's capturer grabs the screen or a window (`capture`); the graphics card encodes it
//!   through Media Foundation (`cardenc`) when it can, otherwise WebRTC encodes it on the processor.
//! - Camera: Media Foundation reads the webcam (`camera`), WebRTC encodes it.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use futures_util::StreamExt;
use livekit::e2ee::key_provider::{KeyProvider, KeyProviderOptions};
use livekit::e2ee::{E2eeOptions, EncryptionType};
use livekit::options::{DegradationPreference, TrackPublishOptions, VideoCodec, VideoEncoderBackend, VideoEncoding};
use livekit::prelude::*;
use livekit::webrtc::native::frame_cryptor::EncryptionState;
use livekit::webrtc::prelude::{RtcVideoSource, VideoResolution};
use livekit::webrtc::stats::RtcStats;
use livekit::webrtc::video_frame::{I420Buffer, VideoBuffer, VideoFrame, VideoRotation};
use livekit::webrtc::video_source::native::NativeVideoSource;
use livekit::webrtc::video_stream::native::NativeVideoStream;
use parking_lot::Mutex;
use serde_json::{json, Value};
use tokio::sync::mpsc;

use crate::camera::{self, CameraSpec};
use crate::capture::{self, CaptureCounters, ShareSpec};
use crate::cardenc::CardEncoder;
use crate::events::Events;
use crate::room::{key_index, user_id};
use crate::shm::FrameSink;

/// Pictures of a camera are handed out under this offset plus the user id (same as the app).
pub const CAMERA_KEY_OFFSET: i64 = 1_000_000_000;
/// The window repaints a stream at most this often; more decoded pictures are dropped uncopied.
const SHOW_FPS: f64 = 30.0;
const PREVIEW_FPS: f64 = 30.0;

// If the encoder cannot keep up, fewer frames are handed to it, so it encodes every frame it gets instead
// of falling far behind and skipping at random. The rate goes back up once it copes again.
const FPS_STEPS: [u32; 5] = [60, 45, 30, 20, 15];
const FPS_BEHIND: f64 = 0.6;
const FPS_BEHIND_SECONDS: u64 = 8;
const FPS_RECOVER_SECONDS: u64 = 45;

pub enum VideoCmd {
    Connect { url: String, token: String, my_id: i64 },
    Disconnect,
    LocalKey { id: String, key: Vec<u8> },
    RemoteKey { user_id: i64, id: String, key: Vec<u8> },
    Watch { user_id: i64, on: bool },
    Share(ShareSpec),
    ShareStop,
    Camera(CameraSpec),
    CameraStop,
    /// The card's encoder or a capture thread gave up mid-way (sent by our own threads).
    Failed { kind: &'static str, generation: u64, reason: String, card: bool },
}

struct Session {
    room: Arc<Room>,
    local: ParticipantIdentity,
}

struct Outgoing {
    generation: u64,
    sid: TrackSid,
    stop: Arc<AtomicBool>,
    thread: Option<std::thread::JoinHandle<()>>,
    stats: Option<tokio::task::JoinHandle<()>>,
}

impl Outgoing {
    fn halt(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(stats) = self.stats.take() {
            stats.abort();
        }
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

struct State {
    events: Events,
    tx: mpsc::UnboundedSender<VideoCmd>,
    session: Option<Session>,
    my_id: i64,
    local_key: Option<(String, Vec<u8>)>,
    remote_keys: HashMap<i64, (String, Vec<u8>)>,
    applied: HashMap<String, String>,
    watching: HashSet<i64>,
    readers: Arc<Mutex<HashMap<i64, (u64, Arc<tokio::sync::Notify>)>>>,
    next_generation: Arc<AtomicU64>,
    key_problem_at: HashMap<i64, Instant>,
    share_spec: Option<ShareSpec>,
    share: Option<Outgoing>,
    camera_spec: Option<CameraSpec>,
    camera: Option<Outgoing>,
    /// The card's encoder failed once in this run: the processor encodes until the app restarts.
    card_failed: bool,
    reported: (Vec<i64>, Vec<i64>),
}

pub fn channel() -> (mpsc::UnboundedSender<VideoCmd>, mpsc::UnboundedReceiver<VideoCmd>) {
    mpsc::unbounded_channel()
}

pub async fn run(events: Events, tx: mpsc::UnboundedSender<VideoCmd>, mut commands: mpsc::UnboundedReceiver<VideoCmd>) {
    let mut state = State {
        events,
        tx,
        session: None,
        my_id: 0,
        local_key: None,
        remote_keys: HashMap::new(),
        applied: HashMap::new(),
        watching: HashSet::new(),
        readers: Arc::new(Mutex::new(HashMap::new())),
        next_generation: Arc::new(AtomicU64::new(1)),
        key_problem_at: HashMap::new(),
        share_spec: None,
        share: None,
        camera_spec: None,
        camera: None,
        card_failed: false,
        reported: (vec![-1], vec![-1]),
    };
    let mut room_events: Option<mpsc::UnboundedReceiver<RoomEvent>> = None;
    let (timer_tx, mut timer_rx) = mpsc::unbounded_channel::<i32>();
    loop {
        tokio::select! {
            cmd = commands.recv() => {
                let Some(cmd) = cmd else { break };
                match cmd {
                    VideoCmd::Connect { url, token, my_id } => {
                        state.close().await;
                        state.my_id = my_id;
                        room_events = state.connect(&url, &token).await;
                    }
                    VideoCmd::Disconnect => {
                        state.share_spec = None;
                        state.camera_spec = None;
                        state.close().await;
                        room_events = None;
                        state.events.send(json!({"ev": "vstate", "state": "closed", "reason": "requested"}));
                    }
                    VideoCmd::LocalKey { id, key } => {
                        let index = key_index(&id);
                        let rotation = state.local_key.as_ref().map(|(old, _)| old != &id).unwrap_or(false);
                        state.local_key = Some((id, key));
                        if state.apply_local_key() && rotation {
                            let tx = timer_tx.clone();
                            tokio::spawn(async move {
                                tokio::time::sleep(Duration::from_secs(3)).await;
                                let _ = tx.send(index);
                            });
                        }
                    }
                    VideoCmd::RemoteKey { user_id, id, key } => {
                        state.remote_keys.insert(user_id, (id, key));
                        state.apply_remote_keys();
                    }
                    VideoCmd::Watch { user_id, on } => {
                        if on { state.watching.insert(user_id); } else { state.watching.remove(&user_id); }
                        state.apply_subscriptions();
                    }
                    VideoCmd::Share(spec) => {
                        state.share_spec = Some(spec);
                        state.start_share().await;
                    }
                    VideoCmd::ShareStop => {
                        state.share_spec = None;
                        state.stop_share().await;
                    }
                    VideoCmd::Camera(spec) => {
                        state.camera_spec = Some(spec);
                        state.start_camera().await;
                    }
                    VideoCmd::CameraStop => {
                        state.camera_spec = None;
                        state.stop_camera().await;
                    }
                    VideoCmd::Failed { kind, generation, reason, card } => {
                        state.failed(kind, generation, reason, card).await;
                    }
                }
            }
            Some(index) = timer_rx.recv() => state.use_local_key_index(index),
            ev = async {
                match room_events.as_mut() {
                    Some(rx) => rx.recv().await,
                    None => std::future::pending().await,
                }
            } => {
                match ev {
                    Some(ev) => {
                        if state.handle(ev) {
                            state.close().await;
                            room_events = None;
                        }
                    }
                    None => {
                        state.close().await;
                        room_events = None;
                        state.events.send(json!({"ev": "vstate", "state": "closed", "reason": "events ended"}));
                    }
                }
            }
        }
    }
    state.close().await;
}

impl State {
    async fn connect(&mut self, url: &str, token: &str) -> Option<mpsc::UnboundedReceiver<RoomEvent>> {
        self.events.send(json!({"ev": "vstate", "state": "connecting"}));
        let provider = KeyProvider::new(KeyProviderOptions { ratchet_window_size: 0, ..Default::default() });
        let mut options = RoomOptions::default();
        options.auto_subscribe = false;
        // Senders stop encoding layers nobody watches (a share nobody looks at costs nothing).
        options.dynacast = true;
        options.adaptive_stream = false;
        options.encryption = Some(E2eeOptions { encryption_type: EncryptionType::Gcm, key_provider: provider });
        options.connect_timeout = Duration::from_secs(10);
        let (room, rx) = match Room::connect(url, token, options).await {
            Ok(v) => v,
            Err(e) => {
                log::warn!("Video connection failed: {e}");
                self.events.send(json!({"ev": "vstate", "state": "closed", "reason": e.to_string()}));
                return None;
            }
        };
        let room = Arc::new(room);
        let local = room.local_participant().identity();
        self.applied.clear();
        self.session = Some(Session { room: room.clone(), local: local.clone() });
        self.apply_local_key();
        if let Some((id, _)) = self.local_key.clone() {
            self.use_local_key_index(key_index(&id));
        }
        self.apply_remote_keys();
        self.apply_subscriptions();
        log::info!("Video connected as {}, {} others in the room", local.0, room.remote_participants().len());
        self.events.send(json!({"ev": "vstate", "state": "connected", "identity": local.0}));
        self.reported = (vec![-1], vec![-1]);
        if self.share_spec.is_some() {
            self.start_share().await;
        }
        if self.camera_spec.is_some() {
            self.start_camera().await;
        }
        self.report_tracks();
        Some(rx)
    }

    async fn close(&mut self) {
        self.halt_share();
        self.halt_camera();
        if let Some(s) = self.session.take() {
            let _ = s.room.close().await;
        }
        for (_, (_, stop)) in self.readers.lock().drain() {
            stop.notify_one();
        }
        self.report_tracks();
    }

    // --- keys --------------------------------------------------------------------------------------

    fn apply_local_key(&mut self) -> bool {
        let (Some(s), Some((id, key))) = (&self.session, &self.local_key) else { return false };
        let Some(provider) = s.room.e2ee_manager().key_provider() else { return false };
        if self.applied.get(&s.local.0) == Some(id) {
            return false;
        }
        provider.set_key(&s.local, key_index(id), key.clone());
        self.applied.insert(s.local.0.clone(), id.clone());
        true
    }

    fn use_local_key_index(&self, index: i32) {
        let Some(s) = &self.session else { return };
        if let Some(current) = self.applied.get(&s.local.0) {
            if key_index(current) != index {
                return;
            }
        }
        for ((identity, _), cryptor) in s.room.e2ee_manager().frame_cryptors() {
            if identity == s.local && cryptor.key_index() != index {
                cryptor.set_key_index(index);
            }
        }
    }

    fn apply_remote_keys(&mut self) {
        let Some(s) = &self.session else { return };
        let Some(provider) = s.room.e2ee_manager().key_provider() else { return };
        for participant in s.room.remote_participants().values() {
            let identity = participant.identity();
            let Some(uid) = user_id(&identity.0) else { continue };
            if let Some((id, key)) = self.remote_keys.get(&uid) {
                if self.applied.get(&identity.0) != Some(id) {
                    provider.set_key(&identity, key_index(id), key.clone());
                    self.applied.insert(identity.0.clone(), id.clone());
                }
            }
        }
    }

    // --- watching ----------------------------------------------------------------------------------

    fn apply_subscriptions(&self) {
        let Some(s) = &self.session else { return };
        for participant in s.room.remote_participants().values() {
            let Some(uid) = user_id(&participant.identity().0) else { continue };
            for publication in participant.track_publications().values() {
                let wanted = uid != self.my_id
                    && publication.kind() == TrackKind::Video
                    && match publication.source() {
                        TrackSource::Camera => true, // cameras are small and part of the call
                        _ => self.watching.contains(&uid),
                    };
                if publication.is_subscribed() != wanted {
                    publication.set_subscribed(wanted);
                    if wanted && publication.source() == TrackSource::Screenshare {
                        // A sharer with extra lower layers: the full picture, as the old app took.
                        publication.set_video_quality(livekit::track::VideoQuality::High);
                    }
                }
            }
        }
    }

    fn report_tracks(&mut self) {
        let (mut screen, mut camera) = (Vec::new(), Vec::new());
        if let Some(s) = &self.session {
            for (identity, participant) in s.room.remote_participants() {
                let Some(uid) = user_id(&identity.0) else { continue };
                for publication in participant.track_publications().values() {
                    match publication.source() {
                        TrackSource::Screenshare => screen.push(uid),
                        TrackSource::Camera => camera.push(uid),
                        _ => {}
                    }
                }
            }
            if self.share.is_some() {
                screen.push(self.my_id);
            }
            if self.camera.is_some() {
                camera.push(self.my_id);
            }
        }
        for list in [&mut screen, &mut camera] {
            list.sort_unstable();
            list.dedup();
        }
        if (screen.clone(), camera.clone()) != self.reported {
            self.events.send(json!({"ev": "vtracks", "screen": screen, "camera": camera}));
            self.reported = (screen, camera);
        }
    }

    fn receive(&self, track: RemoteVideoTrack, key: i64) {
        // Only the newest stream per person is shown: after a key change or a re-subscribe the old one could
        // keep running next to the new one, each picture arriving twice ("90 fps", 2026-11-13).
        let generation = self.next_generation.fetch_add(1, Ordering::SeqCst);
        let stop = Arc::new(tokio::sync::Notify::new());
        if let Some((_, old)) = self.readers.lock().insert(key, (generation, stop.clone())) {
            old.notify_one();
        }
        let readers = self.readers.clone();
        let events = self.events.clone();
        tokio::spawn(async move {
            let mut stream = NativeVideoStream::new(track.rtc_track());
            let mut sink = FrameSink::new(key, events);
            let interval = Duration::from_secs_f64(1.0 / SHOW_FPS);
            let mut due = Instant::now();
            let (started, mut logged) = (Instant::now(), Instant::now());
            let (mut decoded, mut shown) = (0u64, 0u64);
            loop {
                let frame = tokio::select! {
                    frame = stream.next() => frame,
                    _ = stop.notified() => None,
                };
                let Some(frame) = frame else { break };
                if readers.lock().get(&key).map(|r| r.0) != Some(generation) {
                    log::info!("Video from {key}: a newer stream took over, closing this one");
                    break;
                }
                decoded += 1;
                let now = Instant::now();
                if now < due {
                    continue; // dropped without a copy
                }
                // From the previous slot, not from this frame: "33 ms since the last one" with 35 fps
                // coming in showed only every other frame (2026-11-13).
                due = if due + interval < now { now + interval } else { due + interval };
                shown += 1;
                let picture = frame.buffer.to_i420();
                let (w, h) = (picture.width(), picture.height());
                let (sy, su, sv) = picture.strides();
                let (y, u, v) = picture.data();
                sink.write(w, h, y, sy, u, su, v, sv);
                if logged.elapsed() >= Duration::from_secs(60) {
                    logged = Instant::now();
                    log::info!(
                        "Video from {key}: {w}x{h}, {decoded} frames decoded, {shown} shown in {:.0} s",
                        started.elapsed().as_secs_f64()
                    );
                }
            }
            {
                let mut r = readers.lock();
                if r.get(&key).map(|r| r.0) == Some(generation) {
                    r.remove(&key);
                }
            }
            stream.close();
            sink.gone();
        });
    }

    // --- sharing -----------------------------------------------------------------------------------

    fn halt_share(&mut self) {
        if let Some(mut share) = self.share.take() {
            share.halt();
        }
    }

    fn halt_camera(&mut self) {
        if let Some(mut camera) = self.camera.take() {
            camera.halt();
        }
    }

    async fn unpublish(&self, sid: &TrackSid) {
        if let Some(s) = &self.session {
            let _ = s.room.local_participant().unpublish_track(sid).await;
        }
    }

    async fn stop_share(&mut self) {
        if let Some(mut share) = self.share.take() {
            share.halt();
            self.unpublish(&share.sid).await;
        }
        self.report_tracks();
    }

    async fn stop_camera(&mut self) {
        if let Some(mut camera) = self.camera.take() {
            camera.halt();
            self.unpublish(&camera.sid).await;
        }
        self.report_tracks();
    }

    fn problem(&self, kind: &str, text: &str) {
        self.events.send(json!({"ev": "vproblem", "kind": kind, "text": text}));
    }

    async fn start_share(&mut self) {
        let Some(spec) = self.share_spec.clone() else { return };
        if self.session.is_none() {
            return; // started once the connection is up
        }
        self.stop_share().await;
        let generation = self.next_generation.fetch_add(1, Ordering::SeqCst);
        let resolution = VideoResolution { width: spec.width, height: spec.height };
        let want_card = !self.card_failed && matches!(spec.encoder.to_uppercase().as_str(), "" | "NVENC" | "CARD" | "HARDWARE");
        let mut card = None;
        let source = if want_card {
            let source = NativeVideoSource::new_encoded(resolution.clone());
            let tx = self.tx.clone();
            match CardEncoder::start(
                source.clone(),
                spec.width,
                spec.height,
                spec.fps,
                spec.bitrate,
                Box::new(move |reason| {
                    let _ = tx.send(VideoCmd::Failed { kind: "screen", generation, reason, card: true });
                }),
            ) {
                Ok(encoder) => {
                    card = Some(Arc::new(encoder));
                    source
                }
                Err(e) => {
                    log::info!("Card encoder not available ({e}): the processor encodes");
                    NativeVideoSource::new(resolution, true)
                }
            }
        } else {
            NativeVideoSource::new(resolution, true)
        };
        let track = LocalVideoTrack::create_video_track("screen", RtcVideoSource::Native(source.clone()));
        let mut options = TrackPublishOptions::default();
        options.source = TrackSource::Screenshare;
        // H.264 keeps up better than VP8 on big screens (about 52 vs 44 fps at 1440p, 2026-11 tests).
        options.video_codec = VideoCodec::H264;
        options.video_encoding = Some(VideoEncoding { max_bitrate: spec.bitrate, max_framerate: spec.fps as f64 });
        options.simulcast = false;
        if card.is_some() {
            options.video_encoder = VideoEncoderBackend::PreEncoded;
        } else {
            // Keep the frame rate, let WebRTC lower the resolution while the processor cannot keep up (a game
            // next to a 1440p60 share took the software encoder down to 8 fps, 2026-11-13).
            options.degradation_preference = Some(DegradationPreference::MaintainFramerate);
        }
        let room = self.session.as_ref().map(|s| s.room.clone()).unwrap();
        let publication = match room.local_participant().publish_track(LocalTrack::Video(track.clone()), options).await {
            Ok(p) => p,
            Err(e) => {
                log::warn!("Publishing the screen failed: {e}");
                if card.is_some() {
                    self.card_failed = true;
                    drop(card);
                    Box::pin(self.start_share()).await;
                } else {
                    self.problem("screen", "Не удалось начать демонстрацию");
                }
                return;
            }
        };
        if let Some((id, _)) = self.local_key.clone() {
            self.use_local_key_index(key_index(&id));
        }
        let stop = Arc::new(AtomicBool::new(false));
        let counters = Arc::new(CaptureCounters::default());
        let thread = {
            let stop = stop.clone();
            let counters = counters.clone();
            let events = self.events.clone();
            let tx = self.tx.clone();
            let my_id = self.my_id;
            let card = card.clone();
            let spec = spec.clone();
            std::thread::Builder::new()
                .name("screen-capture".into())
                .spawn(move || {
                    let mut preview = FrameSink::new(my_id, events);
                    let preview_every = Duration::from_secs_f64(1.0 / PREVIEW_FPS);
                    let mut preview_at = Instant::now() - preview_every;
                    let result = capture::run(&spec, stop.clone(), counters, |mut picture: I420Buffer, ts| {
                        if preview_at.elapsed() >= preview_every {
                            preview_at = Instant::now();
                            let small = capture::preview_size(picture.width(), picture.height())
                                .map(|(w, h)| picture.scale(w as i32, h as i32));
                            let shown = small.as_ref().unwrap_or(&picture);
                            let (sy, su, sv) = shown.strides();
                            let (y, u, v) = shown.data();
                            preview.write(shown.width(), shown.height(), y, sy, u, su, v, sv);
                        }
                        match &card {
                            Some(encoder) => encoder.push(picture, ts),
                            None => {
                                let frame = VideoFrame { rotation: VideoRotation::VideoRotation0, timestamp_us: ts, frame_metadata: None, buffer: picture };
                                source.capture_frame(&frame);
                            }
                        }
                    });
                    preview.gone();
                    if let Err(reason) = result {
                        if !stop.load(Ordering::Relaxed) {
                            let _ = tx.send(VideoCmd::Failed { kind: "screen", generation, reason, card: false });
                        }
                    }
                })
                .ok()
        };
        let stats = tokio::spawn(share_stats(LocalTrack::Video(track), counters, card, spec.clone(), self.events.clone()));
        self.share = Some(Outgoing { generation, sid: publication.sid(), stop, thread, stats: Some(stats) });
        self.report_tracks();
    }

    async fn start_camera(&mut self) {
        let Some(spec) = self.camera_spec.clone() else { return };
        if self.session.is_none() {
            return;
        }
        self.stop_camera().await;
        let generation = self.next_generation.fetch_add(1, Ordering::SeqCst);
        // The camera tells its real size only once it is open: open it first, then publish.
        let stop = Arc::new(AtomicBool::new(false));
        let (ready_tx, ready_rx) = std::sync::mpsc::channel::<Result<(u32, u32), String>>();
        let source_slot: Arc<Mutex<Option<NativeVideoSource>>> = Arc::new(Mutex::new(None));
        let thread = {
            let stop = stop.clone();
            let events = self.events.clone();
            let tx = self.tx.clone();
            let key = CAMERA_KEY_OFFSET + self.my_id;
            let slot = source_slot.clone();
            let spec = spec.clone();
            std::thread::Builder::new()
                .name("camera".into())
                .spawn(move || {
                    let mut preview = FrameSink::new(key, events);
                    let preview_every = Duration::from_secs_f64(1.0 / PREVIEW_FPS);
                    let mut preview_at = Instant::now() - preview_every;
                    let result = camera::run(&spec, stop.clone(), ready_tx, |picture: I420Buffer, ts| {
                        if preview_at.elapsed() >= preview_every {
                            preview_at = Instant::now();
                            let (sy, su, sv) = picture.strides();
                            let (y, u, v) = picture.data();
                            preview.write(picture.width(), picture.height(), y, sy, u, su, v, sv);
                        }
                        if let Some(source) = slot.lock().as_ref() {
                            let frame = VideoFrame { rotation: VideoRotation::VideoRotation0, timestamp_us: ts, frame_metadata: None, buffer: picture };
                            source.capture_frame(&frame);
                        }
                    });
                    preview.gone();
                    if let Err(reason) = result {
                        if !stop.load(Ordering::Relaxed) {
                            let _ = tx.send(VideoCmd::Failed { kind: "camera", generation, reason, card: false });
                        }
                    }
                })
                .ok()
        };
        let opened = tokio::task::spawn_blocking(move || ready_rx.recv_timeout(Duration::from_secs(10)))
            .await
            .unwrap_or(Err(std::sync::mpsc::RecvTimeoutError::Disconnected));
        let (width, height) = match opened {
            Ok(Ok(size)) => size,
            Ok(Err(reason)) => {
                stop.store(true, Ordering::Relaxed);
                self.camera_spec = None;
                self.problem("camera", &reason);
                return;
            }
            Err(_) => {
                stop.store(true, Ordering::Relaxed);
                self.camera_spec = None;
                self.problem("camera", "Камера не отвечает");
                return;
            }
        };
        let source = NativeVideoSource::new(VideoResolution { width, height }, false);
        *source_slot.lock() = Some(source.clone());
        let track = LocalVideoTrack::create_video_track("camera", RtcVideoSource::Native(source));
        let mut options = TrackPublishOptions::default();
        options.source = TrackSource::Camera;
        options.video_encoding = Some(VideoEncoding { max_bitrate: spec.bitrate, max_framerate: spec.fps as f64 });
        options.simulcast = false;
        let room = self.session.as_ref().map(|s| s.room.clone()).unwrap();
        match room.local_participant().publish_track(LocalTrack::Video(track), options).await {
            Ok(publication) => {
                if let Some((id, _)) = self.local_key.clone() {
                    self.use_local_key_index(key_index(&id));
                }
                log::info!("Camera: {}x{} at {} fps", width, height, spec.fps);
                self.camera = Some(Outgoing { generation, sid: publication.sid(), stop, thread, stats: None });
                self.report_tracks();
            }
            Err(e) => {
                log::warn!("Publishing the camera failed: {e}");
                stop.store(true, Ordering::Relaxed);
                self.camera_spec = None;
                self.problem("camera", "Не удалось включить камеру");
            }
        }
    }

    async fn failed(&mut self, kind: &'static str, generation: u64, reason: String, card: bool) {
        if kind == "screen" {
            if self.share.as_ref().map(|s| s.generation) != Some(generation) {
                return;
            }
            if card {
                // Carry on with the processor, without asking anyone.
                log::warn!("Screen share: the card encoder failed ({reason}), switching to the processor");
                self.card_failed = true;
                self.start_share().await;
            } else {
                log::warn!("Screen share stopped: {reason}");
                self.share_spec = None;
                self.stop_share().await;
                self.problem("screen", &reason);
            }
        } else {
            if self.camera.as_ref().map(|s| s.generation) != Some(generation) {
                return;
            }
            log::warn!("Camera stopped: {reason}");
            self.camera_spec = None;
            self.stop_camera().await;
            self.problem("camera", &reason);
        }
    }

    // --- room events -------------------------------------------------------------------------------

    /// Returns true when the connection has ended.
    fn handle(&mut self, event: RoomEvent) -> bool {
        match event {
            RoomEvent::ParticipantConnected(_) => self.apply_remote_keys(),
            RoomEvent::ParticipantDisconnected(participant) => {
                let identity = participant.identity();
                if let Some(s) = &self.session {
                    if s.room.remote_participants().contains_key(&identity) {
                        return false;
                    }
                }
                self.applied.remove(&identity.0);
                self.report_tracks();
            }
            RoomEvent::TrackPublished { .. } => {
                self.apply_subscriptions();
                self.report_tracks();
            }
            RoomEvent::TrackUnpublished { .. } => self.report_tracks(),
            RoomEvent::TrackSubscribed { track, publication, participant } => {
                if let (RemoteTrack::Video(video), Some(uid)) = (track, user_id(&participant.identity().0)) {
                    let key = if publication.source() == TrackSource::Camera { CAMERA_KEY_OFFSET + uid } else { uid };
                    self.receive(video, key);
                }
            }
            RoomEvent::TrackUnsubscribed { publication, participant, .. } => {
                if let Some(uid) = user_id(&participant.identity().0) {
                    let key = if publication.source() == TrackSource::Camera { CAMERA_KEY_OFFSET + uid } else { uid };
                    if let Some((_, stop)) = self.readers.lock().remove(&key) {
                        stop.notify_one();
                    }
                }
            }
            RoomEvent::E2eeStateChanged { participant, state } => {
                let identity = participant.identity();
                let local = self.session.as_ref().map(|s| s.local == identity).unwrap_or(false);
                if local {
                    if !matches!(state, EncryptionState::Ok | EncryptionState::New) {
                        log::warn!("Our own video encryption: {state:?}");
                    }
                } else if matches!(state, EncryptionState::DecryptionFailed | EncryptionState::MissingKey) {
                    log::warn!(
                        "Cannot decrypt video of {}: {state:?} (applied key {})",
                        identity.0,
                        self.applied.get(&identity.0).map(String::as_str).unwrap_or("none")
                    );
                    if let Some(uid) = user_id(&identity.0) {
                        let now = Instant::now();
                        let due = self.key_problem_at.get(&uid).map(|t| now - *t > Duration::from_secs(3)).unwrap_or(true);
                        if due {
                            self.key_problem_at.insert(uid, now);
                            self.events.send(json!({"ev": "key_problem", "user_id": uid}));
                        }
                    }
                }
            }
            RoomEvent::Reconnecting => self.events.send(json!({"ev": "vstate", "state": "reconnecting"})),
            RoomEvent::Reconnected => {
                self.apply_remote_keys();
                self.events.send(json!({"ev": "vstate", "state": "connected"}));
            }
            RoomEvent::Disconnected { reason } => {
                log::info!("Video connection closed: {reason:?}");
                self.events.send(json!({"ev": "vstate", "state": "closed", "reason": format!("{reason:?}")}));
                return true;
            }
            _ => {}
        }
        false
    }
}

/// Once a second while sharing: what is really captured, encoded and sent (the app shows it under the
/// share, and lowers the frame rate handed to a processor encoder that cannot keep up).
async fn share_stats(
    track: LocalTrack,
    counters: Arc<CaptureCounters>,
    card: Option<Arc<CardEncoder>>,
    spec: ShareSpec,
    events: Events,
) {
    let mut last: Option<(Instant, u32, u32, u64, u32, u32, u32, u32)> = None;
    let mut encoder = Value::Null;
    let mut misses = 0;
    let mut logged = Instant::now();
    let (mut behind, mut fine): (Option<Instant>, Option<Instant>) = (None, None);
    loop {
        tokio::time::sleep(Duration::from_secs(1)).await;
        let now = Instant::now();
        let captured = counters.captured.load(Ordering::Relaxed);
        let handed = counters.handed.load(Ordering::Relaxed);
        let card_encoded = card.as_ref().map(|c| c.encoded.load(Ordering::Relaxed)).unwrap_or(0);
        let card_skipped = card.as_ref().map(|c| c.skipped.load(Ordering::Relaxed)).unwrap_or(0);
        let report = tokio::time::timeout(Duration::from_secs(3), track.get_stats()).await;
        let outbound = match report {
            Ok(Ok(stats)) => stats.into_iter().find_map(|s| match s {
                RtcStats::OutboundRtp(o) => Some(o),
                _ => None,
            }),
            _ => None,
        };
        let (frames_encoded, frames_sent, bytes_sent) = outbound
            .as_ref()
            .map(|o| (o.outbound.frames_encoded, o.outbound.frames_sent, o.sent.bytes_sent))
            .unwrap_or((0, 0, 0));
        let mut stats = json!({});
        if let Some((then, c, h, b, e, s, ce, cs)) = last {
            let dt = (now - then).as_secs_f64().max(0.001);
            let rate = |a: u32, b: u32| ((a.saturating_sub(b)) as f64 / dt * 10.0).round() / 10.0;
            let encoded_fps = if card.is_some() { rate(card_encoded, ce) } else { rate(frames_encoded, e) };
            stats = json!({
                "capture_fps": rate(captured, c).round(),
                "sent_fps": rate(handed, h).round(),
                "encoded_fps": encoded_fps,
                "skipped_fps": rate(card_skipped, cs),
                "network_fps": rate(frames_sent, s),
                "kbps": (8.0 * bytes_sent.saturating_sub(b) as f64 / dt / 1000.0) as u64,
            });
            if card.is_none() && outbound.is_some() {
                adapt_frame_rate(&counters, spec.fps, encoded_fps, now, &mut behind, &mut fine);
            }
        }
        last = Some((now, captured, handed, bytes_sent, frames_encoded, frames_sent, card_encoded, card_skipped));
        match &outbound {
            Some(o) => {
                misses = 0;
                let out = &o.outbound;
                encoder = json!({
                    "sent_width": out.frame_width, "sent_height": out.frame_height,
                    "encoder": out.encoder_implementation,
                    "limited_by": format!("{:?}", out.quality_limitation_reason).to_lowercase().replace("none", ""),
                    "target_kbps": (out.target_bitrate / 1000.0) as u64,
                });
                if let Some(c) = &card {
                    encoder["encoder"] = json!(format!("{} (видеокарта)", c.name()));
                    encoder["sent_width"] = json!(spec.width);
                    encoder["sent_height"] = json!(spec.height);
                }
            }
            None => {
                misses += 1;
                if misses >= 5 {
                    encoder = Value::Null;
                }
            }
        }
        let (sw, sh) = *counters.source_size.lock();
        let mut all = json!({
            "width": spec.width, "height": spec.height, "source_width": sw, "source_height": sh,
            "grab_ms": (counters.grab_us.load(Ordering::Relaxed) as f64 / 100.0).round() / 10.0,
            "backend": "webrtc", "frame_limit": counters.frame_limit.load(Ordering::Relaxed),
        });
        if let (Some(map), Some(extra)) = (all.as_object_mut(), stats.as_object()) {
            map.extend(extra.clone());
        }
        if let (Some(map), Some(extra)) = (all.as_object_mut(), encoder.as_object()) {
            map.extend(extra.clone());
        }
        if logged.elapsed() >= Duration::from_secs(15) {
            logged = Instant::now();
            log::info!("Screen share: {all}");
        }
        events.send(json!({"ev": "vstats", "kind": "screen", "stats": all}));
    }
}

fn adapt_frame_rate(
    counters: &CaptureCounters,
    fps: u32,
    encoded_fps: f64,
    now: Instant,
    behind: &mut Option<Instant>,
    fine: &mut Option<Instant>,
) {
    let limit = counters.frame_limit.load(Ordering::Relaxed);
    let target = if limit > 0 { limit } else { fps };
    if encoded_fps <= 0.0 {
        return;
    }
    if encoded_fps < FPS_BEHIND * target as f64 {
        *fine = None;
        let since = *behind.get_or_insert(now);
        if now - since >= Duration::from_secs(FPS_BEHIND_SECONDS) {
            if let Some(lower) = FPS_STEPS.iter().copied().find(|s| *s < target) {
                counters.frame_limit.store(lower, Ordering::Relaxed);
                log::info!("Screen share: the encoder makes {encoded_fps:.0} of {target} fps, sending {lower} fps");
            }
            *behind = None;
        }
        return;
    }
    *behind = None;
    if limit > 0 {
        if encoded_fps >= 0.9 * target as f64 {
            let since = *fine.get_or_insert(now);
            if now - since >= Duration::from_secs(FPS_RECOVER_SECONDS) {
                let higher = FPS_STEPS.iter().copied().filter(|s| target < *s && *s <= fps).last();
                let next = match higher {
                    Some(h) if h < fps => h,
                    _ => 0,
                };
                counters.frame_limit.store(next, Ordering::Relaxed);
                log::info!("Screen share: the encoder keeps up again, sending {} fps", if next > 0 { next } else { fps });
                *fine = None;
            }
        } else {
            *fine = None;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frame_rate_steps_down_after_falling_behind_and_back_up() {
        let counters = CaptureCounters::default();
        let start = Instant::now();
        let (mut behind, mut fine) = (None, None);
        adapt_frame_rate(&counters, 60, 20.0, start, &mut behind, &mut fine);
        adapt_frame_rate(&counters, 60, 20.0, start + Duration::from_secs(9), &mut behind, &mut fine);
        assert_eq!(counters.frame_limit.load(Ordering::Relaxed), 45);
        adapt_frame_rate(&counters, 60, 44.0, start + Duration::from_secs(10), &mut behind, &mut fine);
        adapt_frame_rate(&counters, 60, 44.0, start + Duration::from_secs(60), &mut behind, &mut fine);
        assert_eq!(counters.frame_limit.load(Ordering::Relaxed), 0);
    }
}
