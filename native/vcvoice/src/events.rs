//! Events to the app: one JSON object per line on stdout, written by one thread so the audio callbacks
//! never block on the pipe. Logging goes to stderr, which the app copies into its log file.

use std::io::Write;
use std::sync::mpsc;

use serde_json::{json, Value};

#[derive(Clone)]
pub struct Events {
    tx: mpsc::Sender<Value>,
}

impl Events {
    pub fn start() -> Self {
        let (tx, rx) = mpsc::channel::<Value>();
        std::thread::Builder::new()
            .name("events-out".into())
            .spawn(move || {
                let stdout = std::io::stdout();
                for event in rx {
                    let mut out = stdout.lock();
                    if writeln!(out, "{event}").and_then(|_| out.flush()).is_err() {
                        // The app is gone: nothing left to do.
                        std::process::exit(0);
                    }
                }
            })
            .expect("events thread");
        Self { tx }
    }

    pub fn send(&self, event: Value) {
        let _ = self.tx.send(event);
    }

    pub fn problem(&self, kind: &str) {
        self.send(json!({"ev": "problem", "kind": kind}));
    }

    pub fn transmitting(&self, on: bool) {
        self.send(json!({"ev": "tx", "on": on}));
    }
}

/// Minimal logger: "LEVEL target: message" lines on stderr.
/// WebRTC's own warnings are mostly ICE noise (STUN/TURN sends failing on virtual adapters such as
/// WireGuard or Hyper-V, 2026-11-17: 80 lines in 15 s): only its errors pass, and any line repeating
/// within a minute is dropped.
pub struct StderrLogger;

static RECENT: std::sync::OnceLock<parking_lot::Mutex<std::collections::HashMap<String, std::time::Instant>>> =
    std::sync::OnceLock::new();

fn repeated(key: String) -> bool {
    let mut recent = RECENT.get_or_init(Default::default).lock();
    let now = std::time::Instant::now();
    if recent.len() > 500 {
        recent.retain(|_, t| now.duration_since(*t).as_secs() < 60);
    }
    match recent.get(&key) {
        Some(t) if now.duration_since(*t).as_secs() < 60 => true,
        _ => {
            recent.insert(key, now);
            false
        }
    }
}

impl log::Log for StderrLogger {
    fn enabled(&self, metadata: &log::Metadata) -> bool {
        let target = metadata.target();
        if target.starts_with("vcvoice") {
            metadata.level() <= log::Level::Info
        } else if target.starts_with("livekit") && !target.contains("webrtc") {
            metadata.level() <= log::Level::Warn
        } else {
            metadata.level() <= log::Level::Error
        }
    }

    fn log(&self, record: &log::Record) {
        if !self.enabled(record.metadata()) {
            return;
        }
        let target = record.target();
        let text = record.args().to_string();
        if !target.starts_with("vcvoice") {
            // Digits differ between repeats (ports, addresses, counters): compare without them.
            let key: String = text.chars().filter(|c| !c.is_ascii_digit()).take(80).collect();
            if repeated(format!("{target}|{key}")) {
                return;
            }
        }
        let _ = writeln!(std::io::stderr(), "{} {}: {}", record.level(), target, text);
    }

    fn flush(&self) {}
}
