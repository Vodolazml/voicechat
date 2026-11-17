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
pub struct StderrLogger;

impl log::Log for StderrLogger {
    fn enabled(&self, metadata: &log::Metadata) -> bool {
        metadata.level() <= log::Level::Info
            || (metadata.level() <= log::Level::Warn && metadata.target().starts_with("livekit"))
    }

    fn log(&self, record: &log::Record) {
        let target = record.target();
        // LiveKit and WebRTC are chatty at info level; keep only their warnings and errors.
        if !target.starts_with("vcvoice") && record.level() > log::Level::Warn {
            return;
        }
        let _ = writeln!(std::io::stderr(), "{} {}: {}", record.level(), target, record.args());
    }

    fn flush(&self) {}
}
