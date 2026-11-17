//! Microphone and speakers, entirely in native code.
//!
//! Capture runs in the device callback: gain -> headroom guard -> WebRTC processing (echo, noise) ->
//! optional RNNoise -> voice gate / push-to-talk -> leveler + limiter -> LiveKit. Playback pulls the mix
//! of everyone's voice and the UI sounds in the output callback. Nothing here wakes Python: the app only
//! hears about state changes (speaking on/off, problems) and asks for stats now and then.

use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicU32, AtomicU64, Ordering};
use std::sync::mpsc;
use std::sync::Arc;
use std::time::{Duration, Instant};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use parking_lot::{Mutex, RwLock};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::dsp::{self, HeadroomGuard, Leveler, Limiter, VoiceGate, FRAME, I16_MAX, SAMPLE_RATE};
use crate::events::Events;

// --- settings -------------------------------------------------------------------------------------

#[derive(Clone, Debug, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub input_device: String,
    pub output_device: String,
    pub input_gain: f32,
    pub output_volume: f32,
    pub noise: String,
    pub echo_cancellation: bool,
    pub auto_gain: bool,
    pub normalize_voices: bool,
    pub mode: String,
    pub auto_threshold: bool,
    pub threshold_db: f32,
    pub user_volumes: HashMap<String, f32>,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            input_device: String::new(),
            output_device: String::new(),
            input_gain: 1.0,
            output_volume: 1.0,
            noise: "standard".into(),
            echo_cancellation: true,
            auto_gain: true,
            normalize_voices: true,
            mode: "voice".into(),
            auto_threshold: true,
            threshold_db: -45.0,
            user_volumes: HashMap::new(),
        }
    }
}

// Our own automatic gain for the outgoing voice (WebRTC's drove speech to 0 dBFS, 2026-11-15).
const VOICE_TARGET_DBFS: f32 = -20.0;
const VOICE_MAX_GAIN: f32 = 4.0;
const VOICE_MIN_GAIN: f32 = 0.5;
const OUT_CEILING: f32 = 0.71; // -3 dBFS
const OVERLOAD_PEAK: f32 = 29204.0; // -1 dBFS straight from the device
const OVERLOAD_FRAMES: u32 = 30;
const FULL_SCALE: f32 = 32700.0;
const SILENT_PEAK: f32 = 3.0;
const SOUND_BLOCKS: u32 = 30;
const SILENT_FIRST: f32 = 3.5;
const SILENT_SECONDS: f32 = 8.0;
const SILENT_REOPENS: u32 = 4;
const PROCESSED_DEAD_SECONDS: f32 = 2.0;

// --- WebRTC audio processing, shared by the capture (near end) and playback (far end) callbacks ----

struct Apm(livekit::webrtc::native::apm::AudioProcessingModule);
// The module is used under a mutex only; WebRTC's APM itself is safe to call from different threads.
unsafe impl Send for Apm {}

fn new_apm(s: &Settings) -> Option<Apm> {
    if !s.echo_cancellation && s.noise == "off" {
        return None;
    }
    Some(Apm(livekit::webrtc::native::apm::AudioProcessingModule::new(
        s.echo_cancellation,
        false, // our Leveler does the gain, see VOICE_TARGET_DBFS
        true,
        s.noise != "off",
    )))
}

// --- mixer ------------------------------------------------------------------------------------------

struct SpeakerBuffer {
    frames: VecDeque<Vec<f32>>,
    leveler: Leveler,
    started: bool,
    last: Option<Vec<f32>>,
}

pub struct Mixer {
    speakers: HashMap<i64, SpeakerBuffer>,
    effects: VecDeque<(Vec<f32>, bool)>,
    pub master_volume: f32,
    pub user_volume: HashMap<i64, f32>,
    pub muted_users: HashSet<i64>,
    pub deafened: bool,
    pub normalize: bool,
    limiter: Limiter,
    received: HashMap<i64, u32>,
    underruns: HashMap<i64, u32>,
}

impl Mixer {
    const START_FRAMES: usize = 2;
    const MAX_FRAMES: usize = 12;

    fn new() -> Self {
        Self {
            speakers: HashMap::new(),
            effects: VecDeque::new(),
            master_volume: 1.0,
            user_volume: HashMap::new(),
            muted_users: HashSet::new(),
            deafened: false,
            normalize: true,
            limiter: Limiter::new(0.89),
            received: HashMap::new(),
            underruns: HashMap::new(),
        }
    }

    pub fn put(&mut self, user_id: i64, frame: &[i16]) {
        if frame.len() != FRAME {
            return;
        }
        *self.received.entry(user_id).or_default() += 1;
        let buffer = self.speakers.entry(user_id).or_insert_with(|| SpeakerBuffer {
            frames: VecDeque::new(),
            leveler: Leveler::new(-20.0, 4.0, 0.5),
            started: false,
            last: None,
        });
        buffer.frames.push_back(frame.iter().map(|s| *s as f32).collect());
        while buffer.frames.len() > Self::MAX_FRAMES {
            buffer.frames.pop_front();
        }
    }

    pub fn remove(&mut self, user_id: i64) {
        self.speakers.remove(&user_id);
    }

    pub fn clear(&mut self) {
        self.speakers.clear();
        self.effects.clear();
    }

    fn play_effect(&mut self, pcm: &[i16], gain: f32, always: bool) {
        self.effects.clear();
        for chunk in pcm.chunks_exact(FRAME) {
            self.effects.push_back((chunk.iter().map(|s| *s as f32 * gain).collect(), always));
        }
    }

    /// One 10 ms frame of everything that should be heard, or None for silence.
    fn mix(&mut self, out: &mut [f32; FRAME]) -> bool {
        out.fill(0.0);
        let mut active = false;
        let quiet_all = self.deafened;
        for (user_id, buffer) in self.speakers.iter_mut() {
            let mut starting = false;
            if !buffer.started {
                if buffer.frames.len() < Self::START_FRAMES {
                    continue;
                }
                buffer.started = true;
                starting = true;
            }
            let Some(mut frame) = buffer.frames.pop_front() else {
                // Underrun: rebuffer, and fade the last frame out mirrored instead of cutting (a click).
                buffer.started = false;
                *self.underruns.entry(*user_id).or_default() += 1;
                if let Some(last) = buffer.last.take() {
                    if !(quiet_all || self.muted_users.contains(user_id)) {
                        let n = FRAME as f32 - 1.0;
                        for (i, s) in out.iter_mut().enumerate() {
                            *s += last[FRAME - 1 - i] * (1.0 - i as f32 / n);
                        }
                        active = true;
                    }
                }
                continue;
            };
            if quiet_all || self.muted_users.contains(user_id) {
                buffer.last = None;
                continue;
            }
            if buffer.last.is_none() && frame.iter().all(|s| *s == 0.0) {
                continue; // they are quiet (most of a call): nothing to level or add
            }
            if self.normalize {
                buffer.leveler.process(&mut frame, true);
            }
            let volume = self.user_volume.get(user_id).copied().unwrap_or(1.0);
            if (volume - 1.0).abs() > 1e-3 {
                for s in frame.iter_mut() {
                    *s *= volume;
                }
            }
            if starting && buffer.last.is_none() {
                dsp::fade_in(&mut frame);
            }
            for (o, s) in out.iter_mut().zip(frame.iter()) {
                *o += *s;
            }
            buffer.last = Some(frame);
            active = true;
        }
        if let Some((effect, always)) = self.effects.pop_front() {
            if always || !self.deafened {
                // The app picks the loudness of each sound (gain with the "sound" command).
                for (o, s) in out.iter_mut().zip(effect.iter()) {
                    *o += *s;
                }
                active = true;
            }
        }
        if !active {
            self.limiter.gain = 1.0;
            return false;
        }
        if (self.master_volume - 1.0).abs() > 1e-3 {
            for s in out.iter_mut() {
                *s *= self.master_volume;
            }
        }
        self.limiter.process(out);
        true
    }

    fn take_stats(&mut self) -> (HashMap<i64, u32>, HashMap<i64, u32>) {
        (std::mem::take(&mut self.received), std::mem::take(&mut self.underruns))
    }
}

// --- shared state ------------------------------------------------------------------------------------

#[derive(Default)]
struct Counters {
    frames_in: u64,
    frames_sent: u64,
    frames_out: u64,
    odd_blocks: u64,
    overflows: u64,
    underflows: u64,
    raw_peak: f32,
    apm_peak: f32,
    out_peak: f32,
    sent_peak: f32,
    level_peak_db: f32,
    overload_frames: u64,
    clipped_frames: u64,
    min_headroom: f32,
    played_peak: f32,
}

pub struct Shared {
    settings: RwLock<Settings>,
    settings_version: AtomicU64,
    apm: Mutex<Option<Apm>>,
    rebuild_processing: AtomicBool,
    pub mixer: Mutex<Mixer>,
    counters: Mutex<Counters>,
    pub muted: AtomicBool,
    pub ptt: AtomicBool,
    pub transmitting: AtomicBool,
    level_db: AtomicU32,
    delay_ms: AtomicI32,
    callbacks: AtomicU64,
    window_raw: AtomicU32,
    window_apm: AtomicU32,
    window_out: AtomicU32,
    window_overload: AtomicU32,
    heard_since_open: AtomicBool,
    sound_blocks: AtomicU32,
    stream_error: AtomicBool,
    running: AtomicBool,
    pub mic_out: Mutex<Option<tokio::sync::mpsc::Sender<Vec<i16>>>>,
    events: Events,
}

impl Shared {
    fn settings(&self) -> Settings {
        self.settings.read().clone()
    }
}

// --- the engine handle --------------------------------------------------------------------------------

enum Command {
    Start,
    Stop,
    Reopen(&'static str),
    Quit,
}

pub struct Engine {
    pub shared: Arc<Shared>,
    commands: mpsc::Sender<Command>,
}

impl Engine {
    pub fn new(events: Events) -> Self {
        let shared = Arc::new(Shared {
            settings: RwLock::new(Settings::default()),
            settings_version: AtomicU64::new(1),
            apm: Mutex::new(None),
            rebuild_processing: AtomicBool::new(false),
            mixer: Mutex::new(Mixer::new()),
            counters: Mutex::new(Counters { min_headroom: 1.0, level_peak_db: -96.0, ..Default::default() }),
            muted: AtomicBool::new(false),
            ptt: AtomicBool::new(false),
            transmitting: AtomicBool::new(false),
            level_db: AtomicU32::new((-96.0f32).to_bits()),
            delay_ms: AtomicI32::new(60),
            callbacks: AtomicU64::new(0),
            window_raw: AtomicU32::new(0),
            window_apm: AtomicU32::new(0),
            window_out: AtomicU32::new(0),
            window_overload: AtomicU32::new(0),
            heard_since_open: AtomicBool::new(false),
            sound_blocks: AtomicU32::new(0),
            stream_error: AtomicBool::new(false),
            running: AtomicBool::new(false),
            mic_out: Mutex::new(None),
            events,
        });
        let (tx, rx) = mpsc::channel();
        let thread_shared = shared.clone();
        std::thread::Builder::new()
            .name("audio-devices".into())
            .spawn(move || device_thread(thread_shared, rx))
            .expect("audio thread");
        Self { shared, commands: tx }
    }

    pub fn apply_settings(&self, settings: Settings) {
        let reopen = {
            let current = self.shared.settings.read();
            current.input_device != settings.input_device || current.output_device != settings.output_device
        };
        let processing_changed = {
            let current = self.shared.settings.read();
            current.echo_cancellation != settings.echo_cancellation || (current.noise == "off") != (settings.noise == "off")
        };
        {
            let mut mixer = self.shared.mixer.lock();
            mixer.master_volume = settings.output_volume;
            mixer.normalize = settings.normalize_voices;
            mixer.user_volume = settings
                .user_volumes
                .iter()
                .filter_map(|(k, v)| k.parse::<i64>().ok().map(|id| (id, *v)))
                .collect();
        }
        if processing_changed {
            *self.shared.apm.lock() = new_apm(&settings);
        }
        *self.shared.settings.write() = settings;
        self.shared.settings_version.fetch_add(1, Ordering::SeqCst);
        if reopen && self.shared.running.load(Ordering::SeqCst) {
            let _ = self.commands.send(Command::Reopen("settings"));
        }
    }

    pub fn start(&self) {
        let _ = self.commands.send(Command::Start);
    }

    pub fn stop(&self) {
        let _ = self.commands.send(Command::Stop);
    }

    pub fn quit(&self) {
        let _ = self.commands.send(Command::Quit);
    }

    pub fn set_muted(&self, muted: bool) {
        self.shared.muted.store(muted, Ordering::SeqCst);
    }

    pub fn set_ptt(&self, down: bool) {
        self.shared.ptt.store(down, Ordering::SeqCst);
    }

    pub fn set_deafened(&self, deafened: bool) {
        self.shared.mixer.lock().deafened = deafened;
    }

    pub fn set_user_muted(&self, user_id: i64, muted: bool) {
        let mut mixer = self.shared.mixer.lock();
        if muted {
            mixer.muted_users.insert(user_id);
        } else {
            mixer.muted_users.remove(&user_id);
        }
    }

    pub fn play(&self, pcm: &[i16], gain: f32, always: bool) {
        self.shared.mixer.lock().play_effect(pcm, gain, always);
    }

    pub fn level(&self) -> (f32, bool) {
        (f32::from_bits(self.shared.level_db.load(Ordering::Relaxed)), self.shared.transmitting.load(Ordering::Relaxed))
    }

    /// What the microphone and speakers did since the last call, in the keys the Python client logs.
    pub fn stats(&self) -> Value {
        let mut c = self.shared.counters.lock();
        let (heard, underruns) = self.shared.mixer.lock().take_stats();
        let s = self.shared.settings.read();
        let v = json!({
            "running": self.shared.running.load(Ordering::SeqCst),
            "mic_frames": c.frames_in, "sent_frames": c.frames_sent, "speaker_frames": c.frames_out,
            "uneven_blocks": c.odd_blocks,
            "mic_peak_db": (c.level_peak_db * 10.0).round() / 10.0,
            "raw_peak_db": round1(dsp::db(c.raw_peak)), "apm_peak_db": round1(dsp::db(c.apm_peak)),
            "out_peak_db": round1(dsp::db(c.out_peak)), "sent_peak_db": round1(dsp::db(c.sent_peak)),
            "overload_frames": c.overload_frames, "clipped_frames": c.clipped_frames,
            "headroom_db": round1(20.0 * c.min_headroom.max(1e-3).log10()),
            "overflows": c.overflows, "underflows": c.underflows,
            "muted": self.shared.muted.load(Ordering::SeqCst), "mode": s.mode,
            "delay_ms": self.shared.delay_ms.load(Ordering::Relaxed),
            "played_peak_db": round1(dsp::db(c.played_peak)),
            "heard": heard.iter().map(|(k, v)| (k.to_string(), json!(v))).collect::<serde_json::Map<_, _>>(),
            "underruns": underruns.iter().map(|(k, v)| (k.to_string(), json!(v))).collect::<serde_json::Map<_, _>>(),
        });
        *c = Counters { min_headroom: 1.0, level_peak_db: -96.0, ..Default::default() };
        v
    }
}

fn round1(v: f32) -> f32 {
    (v * 10.0).round() / 10.0
}

// --- devices ------------------------------------------------------------------------------------------

pub fn device_names() -> (Vec<String>, Vec<String>) {
    let host = cpal::default_host();
    let mut inputs = Vec::new();
    let mut outputs = Vec::new();
    if let Ok(devices) = host.input_devices() {
        for d in devices {
            if let Ok(name) = d.name() {
                if !inputs.contains(&name) {
                    inputs.push(name);
                }
            }
        }
    }
    if let Ok(devices) = host.output_devices() {
        for d in devices {
            if let Ok(name) = d.name() {
                if !outputs.contains(&name) {
                    outputs.push(name);
                }
            }
        }
    }
    (inputs, outputs)
}

/// "2- Динамики (Realtek)" is the same device as "Динамики (Realtek)": Windows numbers duplicates.
fn plain_name(name: &str) -> &str {
    let trimmed = name.trim();
    if let Some(pos) = trimmed.find("- ") {
        if pos > 0 && trimmed[..pos].chars().all(|c| c.is_ascii_digit()) {
            return trimmed[pos + 2..].trim();
        }
    }
    trimmed
}

fn find_device(name: &str, input: bool) -> (Option<cpal::Device>, bool) {
    let host = cpal::default_host();
    let default = if input { host.default_input_device() } else { host.default_output_device() };
    if name.is_empty() {
        return (default, false);
    }
    let devices: Vec<cpal::Device> = if input {
        host.input_devices().map(|d| d.collect()).unwrap_or_default()
    } else {
        host.output_devices().map(|d| d.collect()).unwrap_or_default()
    };
    let wanted = plain_name(name);
    for d in &devices {
        if d.name().map(|n| n == name).unwrap_or(false) {
            return (Some(d.clone()), false);
        }
    }
    for d in &devices {
        if d.name().map(|n| plain_name(&n) == wanted).unwrap_or(false) {
            return (Some(d.clone()), false);
        }
    }
    (default, true) // missing: the Windows default, and the app says so
}

/// Simple streaming linear resampler, used only when a device does not run at 48 kHz.
struct Resampler {
    step: f64,
    pos: f64,
    prev: f32,
}

impl Resampler {
    fn new(from: u32, to: u32) -> Option<Self> {
        if from == to {
            return None;
        }
        Some(Self { step: from as f64 / to as f64, pos: 0.0, prev: 0.0 })
    }

    fn push(&mut self, input: &[f32], out: &mut Vec<f32>) {
        // Positions are relative to `prev` (index -1) followed by `input`.
        let n = input.len() as f64;
        while self.pos < n {
            let i = self.pos.floor();
            let frac = (self.pos - i) as f32;
            let a = if i < 1.0 { if i < 0.0 { self.prev } else { input[0] } } else { input[i as usize - 1] };
            let a = if i == 0.0 { self.prev } else { a };
            let b = input[(i as usize).min(input.len() - 1)];
            let a = if i >= 1.0 { input[i as usize - 1] } else { a };
            out.push(a + (b - a) * frac);
            self.pos += self.step;
        }
        self.pos -= n;
        if let Some(last) = input.last() {
            self.prev = *last;
        }
    }
}

enum Streams {
    Devices { _input: cpal::Stream, _output: cpal::Stream },
    /// VCVOICE_FAKE_AUDIO=tone|silence: a 10 ms clock instead of devices, for tests without sound cards.
    Fake(Arc<AtomicBool>),
}

impl Drop for Streams {
    fn drop(&mut self) {
        if let Streams::Fake(stop) = self {
            stop.store(true, Ordering::SeqCst);
        }
    }
}

fn open_fake(shared: &Arc<Shared>, kind: String) -> Streams {
    let stop = Arc::new(AtomicBool::new(false));
    let capture = Capture::new(shared.clone(), SAMPLE_RATE);
    let playback = Playback::new(shared.clone(), SAMPLE_RATE);
    let flag = stop.clone();
    std::thread::spawn(move || {
        let (mut capture, mut playback) = (capture, playback);
        let mut phase = 0usize;
        let start = Instant::now();
        let mut tick = 0u64;
        while !flag.load(Ordering::SeqCst) {
            let mut block = [0f32; FRAME];
            if kind == "tone" {
                for s in block.iter_mut() {
                    // 300 Hz, half a second on, half off: a "speaker" with pauses.
                    let on = (phase / 24_000) % 2 == 0;
                    *s = if on { 8000.0 * (2.0 * std::f32::consts::PI * 300.0 * phase as f32 / 48_000.0).sin() } else { 0.0 };
                    phase += 1;
                }
            }
            capture.push_interleaved(block.iter().map(|v| *v), 1);
            let mut heard = 0f32;
            for _ in 0..FRAME {
                heard = heard.max(playback.next_sample().abs());
            }
            if heard > 1.0 {
                shared_heard(&playback.shared, heard);
            }
            tick += 1;
            let due = start + Duration::from_millis(10 * tick);
            let now = Instant::now();
            if due > now {
                std::thread::sleep(due - now);
            }
        }
    });
    log::info!("Audio opened: fake devices");
    Streams::Fake(stop)
}

fn shared_heard(shared: &Shared, peak: f32) {
    let mut c = shared.counters.lock();
    c.played_peak = c.played_peak.max(peak);
}

fn device_thread(shared: Arc<Shared>, commands: mpsc::Receiver<Command>) {
    let mut streams: Option<Streams> = None;
    let mut wanted = false;
    let mut monitor = Monitor::new();
    let mut last_device_check = Instant::now();
    let mut device_signature = None;
    let mut pending_signature = None;
    loop {
        let command = commands.recv_timeout(Duration::from_millis(1500));
        let mut reopen: Option<&'static str> = None;
        match command {
            Ok(Command::Start) => {
                wanted = true;
                if streams.is_none() {
                    reopen = Some("start");
                }
            }
            Ok(Command::Stop) => {
                wanted = false;
                streams = None;
                shared.running.store(false, Ordering::SeqCst);
                shared.transmitting.store(false, Ordering::SeqCst);
                shared.mixer.lock().clear();
                log::info!("Audio closed");
                continue;
            }
            Ok(Command::Reopen(why)) => {
                if wanted {
                    reopen = Some(why);
                }
            }
            Ok(Command::Quit) | Err(mpsc::RecvTimeoutError::Disconnected) => return,
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }
        if wanted && reopen.is_none() {
            if shared.stream_error.swap(false, Ordering::SeqCst) {
                log::warn!("An audio device reported an error: reopening");
                shared.events.problem("device_lost");
                reopen = Some("error");
            } else if let Some(why) = monitor.check(&shared) {
                reopen = Some(why);
            } else if last_device_check.elapsed() > Duration::from_secs(6) {
                // A change must look the same on two checks in a row (Windows settles first).
                last_device_check = Instant::now();
                let now = device_names();
                if device_signature.is_none() {
                    device_signature = Some(now);
                } else if device_signature.as_ref() != Some(&now) {
                    if pending_signature.as_ref() == Some(&now) {
                        device_signature = Some(now);
                        pending_signature = None;
                        log::info!("Audio devices changed in Windows: reopening");
                        shared.events.problem("devices_changed");
                        reopen = Some("devices");
                    } else {
                        pending_signature = Some(now);
                    }
                } else {
                    pending_signature = None;
                }
            }
        }
        if let Some(why) = reopen {
            streams = None;
            shared.running.store(false, Ordering::SeqCst);
            match open_streams(&shared) {
                Ok(s) => {
                    streams = Some(s);
                    shared.running.store(true, Ordering::SeqCst);
                    monitor.opened(why);
                }
                Err(e) => {
                    log::warn!("Could not open audio devices ({why}): {e}");
                    shared.events.problem("open_failed");
                }
            }
        }
    }
}

/// Watches a running stream: stuck microphone, processing that eats the voice, overload, stalls.
struct Monitor {
    silent_since: Option<Instant>,
    silence_reopens: u32,
    dead_since: Option<Instant>,
    overloads: VecDeque<(Instant, u32)>,
    overload_told: bool,
    last_callbacks: u64,
    quiet_since: Option<Instant>,
}

impl Monitor {
    fn new() -> Self {
        Self {
            silent_since: None,
            silence_reopens: 0,
            dead_since: None,
            overloads: VecDeque::new(),
            overload_told: false,
            last_callbacks: 0,
            quiet_since: None,
        }
    }

    fn opened(&mut self, why: &str) {
        if why != "silence" {
            self.silence_reopens = 0;
        }
        self.silent_since = None;
        self.dead_since = None;
        self.quiet_since = None;
        self.overloads.clear();
        self.overload_told = false;
    }

    fn check(&mut self, shared: &Shared) -> Option<&'static str> {
        if !shared.running.load(Ordering::SeqCst) {
            return None;
        }
        let now = Instant::now();
        let raw = shared.window_raw.swap(0, Ordering::Relaxed) as f32;
        let apm = shared.window_apm.swap(0, Ordering::Relaxed) as f32;
        let out = shared.window_out.swap(0, Ordering::Relaxed) as f32;
        let over = shared.window_overload.swap(0, Ordering::Relaxed);
        let muted = shared.muted.load(Ordering::SeqCst);
        // Mic set too loud: say so once per opening.
        if over > 0 && !muted {
            self.overloads.push_back((now, over));
        }
        while self.overloads.front().map(|(t, _)| now - *t > Duration::from_secs(60)).unwrap_or(false) {
            self.overloads.pop_front();
        }
        let total: u32 = self.overloads.iter().map(|(_, n)| n).sum();
        if !self.overload_told && total >= OVERLOAD_FRAMES {
            self.overload_told = true;
            log::warn!("The microphone is overloaded: {total} frames at -1 dBFS or louder within 60 s");
            shared.events.problem("mic_overload");
        }
        // The mic hears sound but processing turns it into zeros: rebuild the processing.
        if !muted && raw > 300.0 && out <= 1.0 {
            match self.dead_since {
                None => self.dead_since = Some(now),
                Some(t) if (now - t).as_secs_f32() > PROCESSED_DEAD_SECONDS => {
                    log::warn!("The mic hears sound (raw peak {raw}) but processing outputs silence (after WebRTC {apm}, out {out}): recreating the processing");
                    shared.rebuild_processing.store(true, Ordering::SeqCst);
                    self.dead_since = None;
                }
                _ => {}
            }
        } else {
            self.dead_since = None;
        }
        // A mic that has not delivered one sound since opening is stuck: reopen it a few times.
        if shared.heard_since_open.load(Ordering::SeqCst) || muted {
            self.silent_since = None;
            self.silence_reopens = 0;
        } else if self.silent_since.is_none() {
            self.silent_since = Some(now);
        } else if self.silence_reopens < SILENT_REOPENS
            && (now - self.silent_since.unwrap()).as_secs_f32() > SILENT_FIRST + SILENT_SECONDS * self.silence_reopens as f32
        {
            self.silence_reopens += 1;
            log::warn!("The microphone has delivered only perfect silence since it was opened: reopening ({})", self.silence_reopens);
            shared.events.problem(if self.silence_reopens < SILENT_REOPENS { "mic_silent" } else { "mic_silent_last" });
            return Some("silence");
        }
        // A stream on an unplugged device can simply stop calling back, with no error at all.
        let calls = shared.callbacks.load(Ordering::Relaxed);
        if calls != self.last_callbacks {
            self.last_callbacks = calls;
            self.quiet_since = None;
        } else if self.quiet_since.is_none() {
            self.quiet_since = Some(now);
        } else if (now - self.quiet_since.unwrap()).as_secs_f32() > 3.0 {
            log::warn!("The microphone stopped delivering sound: reopening");
            shared.events.problem("device_lost");
            self.quiet_since = None;
            return Some("stalled");
        }
        None
    }
}

#[cfg(windows)]
fn boost_audio_thread() {
    thread_local!(static DONE: std::cell::Cell<bool> = const { std::cell::Cell::new(false) });
    DONE.with(|done| {
        if done.get() {
            return;
        }
        done.set(true);
        // Only the two device callbacks, which do 10 ms of work at a time, are lifted. WASAPI may already
        // have registered them; registering twice is harmless and reported as an error we ignore.
        unsafe {
            let mut index = 0u32;
            let task: Vec<u16> = "Pro Audio\0".encode_utf16().collect();
            windows_sys::Win32::System::Threading::AvSetMmThreadCharacteristicsW(task.as_ptr(), &mut index);
        }
    });
}

#[cfg(not(windows))]
fn boost_audio_thread() {}

fn open_streams(shared: &Arc<Shared>) -> Result<Streams, String> {
    let settings = shared.settings();
    if let Ok(kind) = std::env::var("VCVOICE_FAKE_AUDIO") {
        *shared.apm.lock() = new_apm(&settings);
        shared.heard_since_open.store(kind == "tone", Ordering::SeqCst);
        return Ok(open_fake(shared, kind));
    }
    let (input, input_missing) = find_device(&settings.input_device, true);
    let (output, output_missing) = find_device(&settings.output_device, false);
    let input = input.ok_or("no microphone")?;
    let output = output.ok_or("no speakers")?;
    if input_missing || output_missing {
        shared.events.problem("fallback");
    }
    let in_config = input.default_input_config().map_err(|e| e.to_string())?;
    let out_config = output.default_output_config().map_err(|e| e.to_string())?;
    {
        let mut apm = shared.apm.lock();
        *apm = new_apm(&settings);
    }
    shared.heard_since_open.store(false, Ordering::SeqCst);
    shared.sound_blocks.store(0, Ordering::SeqCst);

    let capture = Arc::new(Mutex::new(Capture::new(shared.clone(), in_config.sample_rate().0)));
    let in_channels = in_config.channels() as usize;
    let in_stream_config: cpal::StreamConfig = in_config.clone().into();
    let error_shared = shared.clone();
    let on_error = move |e: cpal::StreamError| {
        log::warn!("Audio stream error: {e}");
        error_shared.stream_error.store(true, Ordering::SeqCst);
    };
    macro_rules! input_stream {
        ($t:ty) => {{
            let capture = capture.clone();
            let on_error = on_error.clone();
            input
                .build_input_stream(
                    &in_stream_config,
                    move |data: &[$t], info: &cpal::InputCallbackInfo| {
                        boost_audio_thread();
                        let mut c = capture.lock();
                        c.note_latency(info);
                        c.push_interleaved(data.iter().map(|s| cpal::Sample::to_sample::<f32>(*s) * I16_MAX), in_channels);
                    },
                    on_error,
                    None,
                )
                .map_err(|e| e.to_string())?
        }};
    }
    let input_stream = match in_config.sample_format() {
        cpal::SampleFormat::F32 => input_stream!(f32),
        cpal::SampleFormat::I16 => input_stream!(i16),
        cpal::SampleFormat::I32 => input_stream!(i32),
        cpal::SampleFormat::U16 => input_stream!(u16),
        other => return Err(format!("unsupported microphone format {other:?}")),
    };

    let playback = Arc::new(Mutex::new(Playback::new(shared.clone(), out_config.sample_rate().0)));
    let out_channels = out_config.channels() as usize;
    let out_stream_config: cpal::StreamConfig = out_config.clone().into();
    macro_rules! output_stream {
        ($t:ty) => {{
            let playback = playback.clone();
            let on_error = on_error.clone();
            output
                .build_output_stream(
                    &out_stream_config,
                    move |data: &mut [$t], info: &cpal::OutputCallbackInfo| {
                        boost_audio_thread();
                        let mut p = playback.lock();
                        p.note_latency(info);
                        let frames = data.len() / out_channels;
                        for i in 0..frames {
                            let v = p.next_sample() / I16_MAX;
                            let sample: $t = cpal::Sample::from_sample(v.clamp(-1.0, 1.0));
                            for ch in 0..out_channels {
                                data[i * out_channels + ch] = sample;
                            }
                        }
                    },
                    on_error,
                    None,
                )
                .map_err(|e| e.to_string())?
        }};
    }
    let output_stream = match out_config.sample_format() {
        cpal::SampleFormat::F32 => output_stream!(f32),
        cpal::SampleFormat::I16 => output_stream!(i16),
        cpal::SampleFormat::I32 => output_stream!(i32),
        cpal::SampleFormat::U16 => output_stream!(u16),
        other => return Err(format!("unsupported speaker format {other:?}")),
    };
    input_stream.play().map_err(|e| e.to_string())?;
    output_stream.play().map_err(|e| e.to_string())?;
    log::info!(
        "Audio opened: mic '{}' ({} Hz, {} ch, {:?}), speakers '{}' ({} Hz, {} ch, {:?}), noise {}, echo {}, leveler {}",
        input.name().unwrap_or_default(),
        in_config.sample_rate().0,
        in_config.channels(),
        in_config.sample_format(),
        output.name().unwrap_or_default(),
        out_config.sample_rate().0,
        out_config.channels(),
        out_config.sample_format(),
        settings.noise,
        settings.echo_cancellation,
        settings.auto_gain
    );
    Ok(Streams::Devices { _input: input_stream, _output: output_stream })
}

// --- capture -------------------------------------------------------------------------------------------

struct Capture {
    shared: Arc<Shared>,
    resampler: Option<Resampler>,
    mono: Vec<f32>,
    resampled: Vec<f32>,
    pending: VecDeque<f32>,
    settings: Settings,
    settings_version: u64,
    guard: HeadroomGuard,
    denoise: Option<Box<nnnoiseless::DenoiseState<'static>>>,
    gate: VoiceGate,
    leveler: Leveler,
    limiter: Limiter,
    was_sending: bool,
    frame: [f32; FRAME],
    scratch: [f32; FRAME],
    pcm: [i16; FRAME],
}

impl Capture {
    fn new(shared: Arc<Shared>, rate: u32) -> Self {
        Self {
            shared,
            resampler: Resampler::new(rate, SAMPLE_RATE),
            mono: Vec::with_capacity(4096),
            resampled: Vec::with_capacity(4096),
            pending: VecDeque::with_capacity(4096),
            settings: Settings::default(),
            settings_version: 0,
            guard: HeadroomGuard::new(),
            denoise: None,
            gate: VoiceGate::new(),
            leveler: Leveler::new(VOICE_TARGET_DBFS, VOICE_MAX_GAIN, VOICE_MIN_GAIN),
            limiter: Limiter::new(OUT_CEILING),
            was_sending: false,
            frame: [0.0; FRAME],
            scratch: [0.0; FRAME],
            pcm: [0; FRAME],
        }
    }

    fn note_latency(&mut self, info: &cpal::InputCallbackInfo) {
        let t = info.timestamp();
        if let Some(d) = t.callback.duration_since(&t.capture) {
            // The output side adds its own part; together they are the echo path delay for WebRTC.
            let _ = d;
        }
    }

    fn push_interleaved(&mut self, samples: impl Iterator<Item = f32>, channels: usize) {
        self.shared.callbacks.fetch_add(1, Ordering::Relaxed);
        self.mono.clear();
        let mut acc = 0.0;
        let mut n = 0;
        for s in samples {
            acc += s;
            n += 1;
            if n == channels {
                self.mono.push(acc / channels as f32);
                acc = 0.0;
                n = 0;
            }
        }
        // Raw device level, before anything we do, for the stuck-mic and overload checks.
        let raw = dsp::peak(&self.mono);
        {
            let mut c = self.shared.counters.lock();
            c.raw_peak = c.raw_peak.max(raw);
        }
        self.shared.window_raw.fetch_max(raw as u32, Ordering::Relaxed);
        if raw > SILENT_PEAK {
            let blocks = self.shared.sound_blocks.fetch_add(1, Ordering::Relaxed) + 1;
            if blocks >= SOUND_BLOCKS {
                self.shared.heard_since_open.store(true, Ordering::Relaxed);
            }
        }
        if let Some(r) = self.resampler.as_mut() {
            self.resampled.clear();
            r.push(&self.mono, &mut self.resampled);
            self.pending.extend(self.resampled.iter().copied());
        } else {
            self.pending.extend(self.mono.iter().copied());
        }
        if self.pending.len() > FRAME * 50 {
            self.pending.clear(); // never let a backlog build up
        }
        while self.pending.len() >= FRAME {
            for (i, s) in self.pending.drain(..FRAME).enumerate() {
                self.frame[i] = s;
            }
            self.process_frame();
        }
    }

    fn refresh_settings(&mut self) {
        let version = self.shared.settings_version.load(Ordering::SeqCst);
        if version != self.settings_version || self.shared.rebuild_processing.swap(false, Ordering::SeqCst) {
            let rebuild = version == self.settings_version;
            self.settings_version = version;
            self.settings = self.shared.settings();
            self.gate.auto = self.settings.auto_threshold;
            self.gate.manual_threshold_db = self.settings.threshold_db;
            let want_rnnoise = self.settings.noise == "strong";
            if want_rnnoise && (self.denoise.is_none() || rebuild) {
                self.denoise = Some(nnnoiseless::DenoiseState::new());
            } else if !want_rnnoise {
                self.denoise = None;
            }
            if rebuild {
                *self.shared.apm.lock() = new_apm(&self.settings);
            }
        }
    }

    fn process_frame(&mut self) {
        self.refresh_settings();
        let shared = self.shared.clone();
        let gain = self.settings.input_gain;
        if (gain - 1.0).abs() > 1e-3 {
            for s in self.frame.iter_mut() {
                *s = (*s * gain).clamp(-I16_MAX, I16_MAX);
            }
        }
        let mut overloaded = false;
        if dsp::peak(&self.frame) >= OVERLOAD_PEAK {
            overloaded = true;
            shared.window_overload.fetch_add(1, Ordering::Relaxed);
        }
        self.guard.process(&mut self.frame);
        // WebRTC: echo cancellation and noise suppression.
        {
            let mut apm = shared.apm.lock();
            if let Some(apm) = apm.as_mut() {
                dsp::to_i16(&self.frame, &mut self.pcm);
                let _ = apm.0.set_stream_delay_ms(shared.delay_ms.load(Ordering::Relaxed));
                if apm.0.process_stream(&mut self.pcm, SAMPLE_RATE as i32, 1).is_ok() {
                    for (f, p) in self.frame.iter_mut().zip(self.pcm.iter()) {
                        *f = *p as f32;
                    }
                }
            }
        }
        let apm_peak = dsp::peak(&self.frame);
        shared.window_apm.fetch_max(apm_peak as u32, Ordering::Relaxed);
        // RNNoise runs on every frame: as a recurrent network it must hear the pauses too (0.5.8).
        let mut probability = None;
        if let Some(denoise) = self.denoise.as_mut() {
            let p = denoise.process_frame(&mut self.scratch, &self.frame);
            self.frame.copy_from_slice(&self.scratch);
            probability = Some(p);
        }
        let out_peak = dsp::peak(&self.frame);
        shared.window_out.fetch_max(out_peak as u32, Ordering::Relaxed);
        let is_voice = self.gate.process(&self.frame, probability);
        shared.level_db.store(self.gate.level_db.to_bits(), Ordering::Relaxed);
        let muted = shared.muted.load(Ordering::Relaxed);
        let send = !muted
            && if self.settings.mode == "ptt" { shared.ptt.load(Ordering::Relaxed) } else { is_voice };
        if send != shared.transmitting.swap(send, Ordering::Relaxed) {
            shared.events.transmitting(send);
        }
        let was_sending = std::mem::replace(&mut self.was_sending, send);
        let mut sent_peak = 0.0;
        let mut frame_to_send: Option<&[f32]> = None;
        if !muted {
            if send || was_sending {
                if self.settings.auto_gain {
                    self.leveler.process(&mut self.frame, send);
                }
                self.limiter.process(&mut self.frame);
                if !was_sending {
                    dsp::fade_in(&mut self.frame);
                } else if !send {
                    dsp::fade_out(&mut self.frame);
                }
                sent_peak = dsp::peak(&self.frame);
                frame_to_send = Some(&self.frame);
            }
            // Between words the stream carries silence (Opus DTX makes it almost free): stopping and
            // restarting the stream on every word made friends' jitter buffers crackle.
            let mut out = vec![0i16; FRAME];
            if let Some(f) = frame_to_send {
                dsp::to_i16(f, &mut out);
            }
            if let Some(tx) = shared.mic_out.lock().as_ref() {
                let _ = tx.try_send(out);
            }
        }
        let mut c = shared.counters.lock();
        c.frames_in += 1;
        if send {
            c.frames_sent += 1;
        }
        if overloaded {
            c.overload_frames += 1;
        }
        if apm_peak >= FULL_SCALE {
            c.clipped_frames += 1;
        }
        c.apm_peak = c.apm_peak.max(apm_peak);
        c.out_peak = c.out_peak.max(out_peak);
        c.sent_peak = c.sent_peak.max(sent_peak);
        c.level_peak_db = c.level_peak_db.max(self.gate.level_db);
        c.min_headroom = c.min_headroom.min(self.guard.gain);
    }
}

// --- playback -------------------------------------------------------------------------------------------

struct Playback {
    shared: Arc<Shared>,
    resampler: Option<Resampler>,
    queue: VecDeque<f32>,
    frame: [f32; FRAME],
    pcm: [i16; FRAME],
    out: Vec<f32>,
}

impl Playback {
    fn new(shared: Arc<Shared>, rate: u32) -> Self {
        Self {
            shared,
            resampler: Resampler::new(SAMPLE_RATE, rate),
            queue: VecDeque::with_capacity(8192),
            frame: [0.0; FRAME],
            pcm: [0; FRAME],
            out: Vec::with_capacity(2048),
        }
    }

    fn note_latency(&mut self, info: &cpal::OutputCallbackInfo) {
        let t = info.timestamp();
        if let Some(d) = t.playback.duration_since(&t.callback) {
            // Echo path delay for WebRTC: what is about to play plus the capture side (~10-20 ms).
            let ms = d.as_millis() as i32 + self.queue.len() as i32 / 48 + 20;
            self.shared.delay_ms.store(ms.clamp(10, 500), Ordering::Relaxed);
        }
    }

    fn next_sample(&mut self) -> f32 {
        if self.queue.is_empty() {
            self.refill();
        }
        self.queue.pop_front().unwrap_or(0.0)
    }

    fn refill(&mut self) {
        let active = self.shared.mixer.lock().mix(&mut self.frame);
        {
            let mut c = self.shared.counters.lock();
            c.frames_out += 1;
        }
        if !active {
            self.frame.fill(0.0);
        }
        // The echo canceller hears exactly what the speakers play (UI sounds included).
        if let Some(apm) = self.shared.apm.lock().as_mut() {
            if self.shared.settings.read().echo_cancellation {
                dsp::to_i16(&self.frame, &mut self.pcm);
                let _ = apm.0.process_reverse_stream(&mut self.pcm, SAMPLE_RATE as i32, 1);
            }
        }
        if let Some(r) = self.resampler.as_mut() {
            self.out.clear();
            r.push(&self.frame, &mut self.out);
            self.queue.extend(self.out.iter().copied());
        } else {
            self.queue.extend(self.frame.iter().copied());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plain_name_strips_windows_numbering() {
        assert_eq!(plain_name("2- Динамики (Realtek)"), "Динамики (Realtek)");
        assert_eq!(plain_name("Mic - USB"), "Mic - USB");
    }

    #[test]
    fn resampler_keeps_rate() {
        let mut r = Resampler::new(44_100, 48_000).unwrap();
        let mut out = Vec::new();
        for _ in 0..100 {
            r.push(&[1.0; 441], &mut out);
        }
        assert!((out.len() as i64 - 48_000).abs() < 3, "{}", out.len());
    }

    #[test]
    fn mixer_levels_and_mutes() {
        let mut m = Mixer::new();
        let frame = vec![1000i16; FRAME];
        for _ in 0..4 {
            m.put(5, &frame);
        }
        let mut out = [0.0; FRAME];
        assert!(m.mix(&mut out));
        m.muted_users.insert(5);
        assert!(!m.mix(&mut out));
        m.deafened = true;
        m.play_effect(&vec![1000i16; FRAME * 2], 1.0, true);
        assert!(m.mix(&mut out), "spoken prompts play while deafened");
    }
}
