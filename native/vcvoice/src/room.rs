//! The voice connection: our own LiveKit participant "<id>-audio" that publishes the microphone and
//! receives everyone's voice, end-to-end encrypted with the same per-sender keys as the app's video
//! connection. Voices are decoded and decrypted natively and go straight into the mixer.

use std::borrow::Cow;
use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use futures_util::StreamExt;
use livekit::e2ee::key_provider::{KeyProvider, KeyProviderOptions};
use livekit::e2ee::{E2eeOptions, EncryptionType};
use livekit::options::TrackPublishOptions;
use livekit::prelude::*;
use livekit::webrtc::audio_frame::AudioFrame;
use livekit::webrtc::audio_source::native::NativeAudioSource;
use livekit::webrtc::audio_source::{AudioSourceOptions, RtcAudioSource};
use livekit::webrtc::audio_stream::native::NativeAudioStream;
use livekit::webrtc::native::frame_cryptor::EncryptionState;
use parking_lot::Mutex;
use serde_json::json;
use tokio::sync::mpsc;

use crate::dsp::{FRAME, SAMPLE_RATE};
use crate::engine::Shared;
use crate::events::Events;

pub enum RoomCmd {
    Connect { url: String, token: String, my_id: i64 },
    Disconnect,
    LocalKey { id: String, key: Vec<u8> },
    RemoteKey { user_id: i64, id: String, key: Vec<u8> },
    Mute(bool),
}

/// Both sides derive the key slot from the key id, so a rotated key never collides with the old one.
pub fn key_index(key_id: &str) -> i32 {
    key_id.get(..4).and_then(|h| i32::from_str_radix(h, 16).ok()).map(|v| v % 16).unwrap_or(0)
}

/// "u7" (the app's own connection, and older apps' voice) and "7-audio" (this engine) are user 7.
pub fn user_id(identity: &str) -> Option<i64> {
    let raw = identity.strip_prefix('u').unwrap_or_else(|| identity.strip_suffix("-audio").unwrap_or(identity));
    raw.parse().ok()
}

struct Session {
    room: Arc<Room>,
    local: ParticipantIdentity,
    my_id: i64,
    track: LocalAudioTrack,
    pump: tokio::task::JoinHandle<()>,
}

pub struct RoomState {
    shared: Arc<Shared>,
    events: Events,
    session: Option<Session>,
    local_key: Option<(String, Vec<u8>)>,
    remote_keys: HashMap<i64, (String, Vec<u8>)>,
    applied: HashMap<String, String>,
    generation: Arc<Mutex<HashMap<i64, u64>>>,
    next_generation: Arc<AtomicU64>,
    key_problem_at: HashMap<i64, Instant>,
    muted: bool,
    speakers: HashSet<i64>,
}

pub async fn run(shared: Arc<Shared>, events: Events, mut commands: mpsc::UnboundedReceiver<RoomCmd>) {
    let mut state = RoomState {
        shared,
        events,
        session: None,
        local_key: None,
        remote_keys: HashMap::new(),
        applied: HashMap::new(),
        generation: Arc::new(Mutex::new(HashMap::new())),
        next_generation: Arc::new(AtomicU64::new(1)),
        key_problem_at: HashMap::new(),
        muted: false,
        speakers: HashSet::new(),
    };
    let mut room_events: Option<mpsc::UnboundedReceiver<RoomEvent>> = None;
    let (timer_tx, mut timer_rx) = mpsc::unbounded_channel::<i32>();
    loop {
        tokio::select! {
            cmd = commands.recv() => {
                let Some(cmd) = cmd else { break };
                match cmd {
                    RoomCmd::Connect { url, token, my_id } => {
                        state.close().await;
                        room_events = state.connect(&url, &token, my_id).await;
                    }
                    RoomCmd::Disconnect => {
                        state.close().await;
                        room_events = None;
                        state.events.send(json!({"ev": "state", "state": "closed", "reason": "requested"}));
                    }
                    RoomCmd::LocalKey { id, key } => {
                        let index = key_index(&id);
                        let rotation = state.local_key.as_ref().map(|(old, _)| old != &id).unwrap_or(false);
                        state.local_key = Some((id, key));
                        if state.apply_local_key() && rotation {
                            // Encrypt with the new key a moment later, so friends have fetched it by then.
                            let tx = timer_tx.clone();
                            tokio::spawn(async move {
                                tokio::time::sleep(Duration::from_secs(3)).await;
                                let _ = tx.send(index);
                            });
                        }
                    }
                    RoomCmd::RemoteKey { user_id, id, key } => {
                        state.remote_keys.insert(user_id, (id, key));
                        state.apply_remote_keys();
                    }
                    RoomCmd::Mute(muted) => {
                        state.muted = muted;
                        if let Some(s) = &state.session {
                            if muted { s.track.mute() } else { s.track.unmute() }
                        }
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
                        state.events.send(json!({"ev": "state", "state": "closed", "reason": "events ended"}));
                    }
                }
            }
        }
    }
    state.close().await;
}

impl RoomState {
    async fn connect(&mut self, url: &str, token: &str, my_id: i64) -> Option<mpsc::UnboundedReceiver<RoomEvent>> {
        self.events.send(json!({"ev": "state", "state": "connecting"}));
        // No key ratcheting: we rotate keys ourselves (same settings as the app's video connection).
        let provider = KeyProvider::new(KeyProviderOptions { ratchet_window_size: 0, ..Default::default() });
        let mut options = RoomOptions::default();
        options.auto_subscribe = false;
        options.dynacast = false;
        options.adaptive_stream = false;
        options.encryption = Some(E2eeOptions { encryption_type: EncryptionType::Gcm, key_provider: provider });
        options.connect_timeout = Duration::from_secs(10);
        let (room, rx) = match Room::connect(url, token, options).await {
            Ok(v) => v,
            Err(e) => {
                log::warn!("Voice connection failed: {e}");
                self.events.send(json!({"ev": "state", "state": "closed", "reason": e.to_string()}));
                return None;
            }
        };
        let room = Arc::new(room);
        let local = room.local_participant().identity();
        self.applied.clear();
        let source = NativeAudioSource::new(
            AudioSourceOptions { echo_cancellation: false, noise_suppression: false, auto_gain_control: false },
            SAMPLE_RATE,
            1,
            100,
        );
        let track = LocalAudioTrack::create_audio_track("microphone", RtcAudioSource::Native(source.clone()));
        if self.muted {
            track.mute();
        }
        let (mic_tx, mut mic_rx) = mpsc::channel::<Vec<i16>>(8);
        let pump = tokio::spawn(async move {
            while let Some(samples) = mic_rx.recv().await {
                let frame = AudioFrame {
                    data: Cow::Owned(samples),
                    sample_rate: SAMPLE_RATE,
                    num_channels: 1,
                    samples_per_channel: FRAME as u32,
                };
                if let Err(e) = source.capture_frame(&frame).await {
                    log::debug!("Mic frame dropped: {e}");
                }
            }
        });
        self.session = Some(Session { room: room.clone(), local: local.clone(), my_id, track: track.clone(), pump });
        self.apply_local_key();
        let mut publish = TrackPublishOptions::default();
        publish.source = TrackSource::Microphone;
        publish.dtx = true;
        publish.red = true;
        if let Err(e) = room.local_participant().publish_track(LocalTrack::Audio(track), publish).await {
            log::warn!("Publishing the microphone failed: {e}");
        }
        if let Some((id, _)) = self.local_key.clone() {
            self.use_local_key_index(key_index(&id));
        }
        *self.shared.mic_out.lock() = Some(mic_tx);
        self.apply_remote_keys();
        for participant in room.remote_participants().values() {
            for publication in participant.track_publications().values() {
                self.maybe_subscribe(publication, &participant.identity());
            }
        }
        log::info!(
            "Voice connected as {}, key slot {}, {} others in the room",
            local.0,
            self.local_key.as_ref().map(|(id, _)| key_index(id)).unwrap_or(-1),
            room.remote_participants().len()
        );
        self.events.send(json!({"ev": "state", "state": "connected", "identity": local.0}));
        self.report_participants();
        Some(rx)
    }

    async fn close(&mut self) {
        *self.shared.mic_out.lock() = None;
        if let Some(s) = self.session.take() {
            s.pump.abort();
            let _ = s.room.close().await;
        }
        self.generation.lock().clear();
        self.shared.mixer.lock().clear();
        if !self.speakers.is_empty() {
            self.speakers.clear();
            self.events.send(json!({"ev": "speakers", "ids": []}));
        }
    }

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
                return; // a newer rotation happened meanwhile; its own timer switches to it
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

    fn maybe_subscribe(&self, publication: &RemoteTrackPublication, identity: &ParticipantIdentity) {
        let Some(s) = &self.session else { return };
        let mine = user_id(&identity.0) == Some(s.my_id);
        let wanted = !mine && publication.kind() == TrackKind::Audio && publication.source() == TrackSource::Microphone;
        if wanted && !publication.is_subscribed() {
            publication.set_subscribed(true);
        }
    }

    fn report_participants(&self) {
        let Some(s) = &self.session else { return };
        let ids: HashSet<i64> = s.room.remote_participants().keys().filter_map(|i| user_id(&i.0)).collect();
        self.events.send(json!({"ev": "participants", "ids": ids}));
    }

    /// Returns true when the connection has ended.
    fn handle(&mut self, event: RoomEvent) -> bool {
        match event {
            RoomEvent::ParticipantConnected(_) => {
                self.apply_remote_keys();
                self.report_participants();
            }
            RoomEvent::ParticipantDisconnected(participant) => {
                let identity = participant.identity();
                if let Some(s) = &self.session {
                    if s.room.remote_participants().contains_key(&identity) {
                        log::info!("Old connection of {} closed; the new one stays", identity.0);
                        return false;
                    }
                }
                self.applied.remove(&identity.0);
                if let Some(uid) = user_id(&identity.0) {
                    let still_here = self
                        .session
                        .as_ref()
                        .map(|s| s.room.remote_participants().keys().any(|i| user_id(&i.0) == Some(uid)))
                        .unwrap_or(false);
                    if !still_here {
                        self.shared.mixer.lock().remove(uid);
                    }
                }
                self.report_participants();
            }
            RoomEvent::TrackPublished { publication, participant } => {
                self.maybe_subscribe(&publication, &participant.identity());
            }
            RoomEvent::TrackSubscribed { track, publication, participant } => {
                if let (RemoteTrack::Audio(audio), TrackSource::Microphone) = (track, publication.source()) {
                    if let Some(uid) = user_id(&participant.identity().0) {
                        self.receive(audio, uid);
                    }
                }
            }
            RoomEvent::ActiveSpeakersChanged { speakers } => {
                let ids: HashSet<i64> = speakers.iter().filter_map(|p| user_id(&p.identity().0)).collect();
                if ids != self.speakers {
                    self.speakers = ids.clone();
                    self.events.send(json!({"ev": "speakers", "ids": ids}));
                }
            }
            RoomEvent::ConnectionQualityChanged { quality, participant } => {
                if let Some(uid) = user_id(&participant.identity().0) {
                    let q = match quality {
                        ConnectionQuality::Excellent => 3,
                        ConnectionQuality::Good => 2,
                        ConnectionQuality::Poor => 1,
                        _ => 0,
                    };
                    self.events.send(json!({"ev": "quality", "user_id": uid, "quality": q}));
                }
            }
            RoomEvent::E2eeStateChanged { participant, state } => {
                let identity = participant.identity();
                let local = self.session.as_ref().map(|s| s.local == identity).unwrap_or(false);
                if local {
                    if !matches!(state, EncryptionState::Ok | EncryptionState::New) {
                        log::warn!("Our own encryption: {state:?}");
                    }
                } else if matches!(state, EncryptionState::DecryptionFailed | EncryptionState::MissingKey) {
                    log::warn!(
                        "Cannot decrypt {}: {state:?} (applied key {})",
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
                } else if !matches!(state, EncryptionState::New) {
                    log::info!("Encryption with {}: {state:?}", identity.0);
                }
            }
            RoomEvent::Reconnecting => self.events.send(json!({"ev": "state", "state": "reconnecting"})),
            RoomEvent::Reconnected => {
                self.apply_remote_keys();
                self.events.send(json!({"ev": "state", "state": "connected"}));
            }
            RoomEvent::Disconnected { reason } => {
                log::info!("Voice connection closed: {reason:?}");
                self.events.send(json!({"ev": "state", "state": "closed", "reason": format!("{reason:?}")}));
                return true;
            }
            _ => {}
        }
        false
    }

    fn receive(&self, track: RemoteAudioTrack, uid: i64) {
        // After someone leaves and comes back, their old stream can still be open: only the newest feeds
        // the mixer (two feeding one buffer garbled the voice).
        let generation = self.next_generation.fetch_add(1, Ordering::SeqCst);
        self.generation.lock().insert(uid, generation);
        let generations = self.generation.clone();
        let shared = self.shared.clone();
        tokio::spawn(async move {
            let mut stream = NativeAudioStream::new(track.rtc_track(), SAMPLE_RATE as i32, 1);
            while let Some(frame) = stream.next().await {
                if generations.lock().get(&uid) != Some(&generation) {
                    break;
                }
                let data: &[i16] = &frame.data;
                if data.len() == FRAME {
                    shared.mixer.lock().put(uid, data);
                } else {
                    for chunk in data.chunks_exact(FRAME) {
                        shared.mixer.lock().put(uid, chunk);
                    }
                }
            }
            let mut g = generations.lock();
            if g.get(&uid) == Some(&generation) {
                g.remove(&uid);
                shared.mixer.lock().remove(uid);
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identities_and_key_slots_match_the_python_client() {
        assert_eq!(user_id("u7"), Some(7));
        assert_eq!(user_id("7-audio"), Some(7));
        assert_eq!(user_id("7"), Some(7));
        assert_eq!(user_id("x"), None);
        assert_eq!(key_index("00ff1234"), 255 % 16);
        assert_eq!(key_index("zz"), 0);
    }
}
