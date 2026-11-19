//! vcvoice: the VoiceChat voice engine. The app starts it once and talks to it over stdin/stdout, one JSON
//! object per line. Every 10 ms of audio is handled here, natively; the app only sends commands (join,
//! mute, keys, settings) and gets events (state, who is speaking, problems, stats on request).

mod camera;
mod capture;
mod cardenc;
mod dsp;
mod engine;
mod events;
mod keys;
mod room;
mod shm;
mod video;

use std::io::BufRead;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use base64::Engine as _;
use serde_json::{json, Value};

use engine::{Engine, Settings};
use events::Events;
use room::RoomCmd;
use video::VideoCmd;

fn b64(value: &Value) -> Vec<u8> {
    value.as_str().and_then(|s| base64::engine::general_purpose::STANDARD.decode(s).ok()).unwrap_or_default()
}

/// Same physical pixel coordinates as the app (monitors, windows), and real window sizes for capture.
#[cfg(windows)]
fn dpi_aware() {
    use windows_sys::Win32::UI::HiDpi::{SetProcessDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2};
    unsafe {
        SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    }
}

#[cfg(not(windows))]
fn dpi_aware() {}

fn num(value: &Value, default: i64) -> i64 {
    value.as_i64().or_else(|| value.as_f64().map(|f| f as i64)).unwrap_or(default)
}

fn main() {
    dpi_aware();
    log::set_logger(&events::StderrLogger).ok();
    log::set_max_level(log::LevelFilter::Info);
    let events = Events::start();
    if std::env::args().any(|a| a == "--selftest") {
        selftest();
        return;
    }
    if std::env::args().any(|a| a == "--video") {
        video_main(events);
        return;
    }
    let engine = Arc::new(Engine::new(events.clone()));
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .thread_name("voice-net")
        .enable_all()
        .build()
        .expect("runtime");
    let (room_tx, room_rx) = tokio::sync::mpsc::unbounded_channel();
    runtime.spawn(room::run(engine.shared.clone(), events.clone(), room_rx));
    let level_on = Arc::new(AtomicBool::new(false));
    {
        // The settings screen's level meter: 15 updates a second, only while the app asks for it.
        let engine = engine.clone();
        let events = events.clone();
        let level_on = level_on.clone();
        std::thread::Builder::new()
            .name("level".into())
            .spawn(move || loop {
                if level_on.load(Ordering::Relaxed) {
                    let (db, tx) = engine.level();
                    events.send(json!({"ev": "level", "db": (db * 10.0).round() / 10.0, "tx": tx}));
                    std::thread::sleep(Duration::from_millis(66));
                } else {
                    std::thread::park_timeout(Duration::from_secs(1));
                }
            })
            .expect("level thread");
    }
    let keys = keys::Keys::start(engine.clone(), events.clone());
    events.send(json!({"ev": "ready", "version": env!("CARGO_PKG_VERSION")}));
    log::info!("vcvoice {} started", env!("CARGO_PKG_VERSION"));

    let stdin = std::io::stdin();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        if line.trim().is_empty() {
            continue;
        }
        let msg: Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(e) => {
                log::warn!("Bad command: {e}");
                continue;
            }
        };
        let cmd = msg["cmd"].as_str().unwrap_or("");
        match cmd {
            "settings" => match serde_json::from_value::<Settings>(msg["settings"].clone()) {
                Ok(s) => engine.apply_settings(s),
                Err(e) => log::warn!("Bad settings: {e}"),
            },
            "start" => engine.start(),
            "stop" => engine.stop(),
            "connect" => {
                let _ = room_tx.send(RoomCmd::Connect {
                    url: msg["url"].as_str().unwrap_or("").to_string(),
                    token: msg["token"].as_str().unwrap_or("").to_string(),
                    my_id: msg["my_id"].as_i64().unwrap_or(0),
                });
            }
            "disconnect" => {
                let _ = room_tx.send(RoomCmd::Disconnect);
            }
            "local_key" => {
                let _ = room_tx.send(RoomCmd::LocalKey { id: msg["id"].as_str().unwrap_or("").into(), key: b64(&msg["key"]) });
            }
            "remote_key" => {
                let _ = room_tx.send(RoomCmd::RemoteKey {
                    user_id: msg["user_id"].as_i64().unwrap_or(0),
                    id: msg["id"].as_str().unwrap_or("").into(),
                    key: b64(&msg["key"]),
                });
            }
            "mute" => {
                let on = msg["on"].as_bool().unwrap_or(false);
                engine.set_muted(on);
                let _ = room_tx.send(RoomCmd::Mute(on));
            }
            "deafen" => engine.set_deafened(msg["on"].as_bool().unwrap_or(false)),
            "ptt" => engine.set_ptt(msg["down"].as_bool().unwrap_or(false)),
            "mute_user" => engine.set_user_muted(msg["user_id"].as_i64().unwrap_or(0), msg["on"].as_bool().unwrap_or(false)),
            "sound" => {
                let bytes = b64(&msg["pcm"]);
                let pcm: Vec<i16> = bytes.chunks_exact(2).map(|b| i16::from_le_bytes([b[0], b[1]])).collect();
                engine.play(&pcm, msg["gain"].as_f64().unwrap_or(1.0) as f32, msg["always"].as_bool().unwrap_or(false));
            }
            "stats" => {
                let mut stats = engine.stats();
                stats["ev"] = json!("stats");
                stats["id"] = msg["id"].clone();
                events.send(stats);
            }
            "keys" => {
                // {"name": [virtual key codes]}; {} stops reading the keyboard.
                let combos = msg["combos"]
                    .as_object()
                    .map(|m| {
                        m.iter()
                            .filter_map(|(name, codes)| {
                                let codes: Vec<i32> =
                                    codes.as_array()?.iter().filter_map(|c| c.as_i64().map(|c| c as i32)).collect();
                                (!codes.is_empty()).then(|| (name.clone(), codes))
                            })
                            .collect()
                    })
                    .unwrap_or_default();
                keys.set(combos);
            }
            "level" => level_on.store(msg["on"].as_bool().unwrap_or(false), Ordering::Relaxed),
            "devices" => {
                let (inputs, outputs) = engine::device_names();
                events.send(json!({"ev": "devices", "inputs": inputs, "outputs": outputs, "id": msg["id"]}));
            }
            "quit" => break,
            other => log::warn!("Unknown command {other}"),
        }
    }
    // stdin closed (the app quit or crashed) or "quit": leave the room cleanly and exit.
    let _ = room_tx.send(RoomCmd::Disconnect);
    engine.stop();
    std::thread::sleep(Duration::from_millis(300));
    engine.quit();
    runtime.shutdown_timeout(Duration::from_secs(2));
}

/// `vcvoice --video`: the same program as a second process that runs only the video (screen shares,
/// cameras). The app starts it while there is video and ends it after: its memory is gone between shares,
/// and a crash in a capture, encoder or camera driver cannot take the voice down.
fn video_main(events: Events) {
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .thread_name("video-net")
        .enable_all()
        .build()
        .expect("video runtime");
    let (video_tx, video_rx) = video::channel();
    runtime.spawn(video::run(events.clone(), video_tx.clone(), video_rx));
    events.send(json!({"ev": "ready", "version": env!("CARGO_PKG_VERSION"), "mode": "video"}));
    log::info!("vcvoice {} video started", env!("CARGO_PKG_VERSION"));
    let stdin = std::io::stdin();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        if line.trim().is_empty() {
            continue;
        }
        let msg: Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(e) => {
                log::warn!("Bad command: {e}");
                continue;
            }
        };
        match msg["cmd"].as_str().unwrap_or("") {
            "local_key" => {
                let _ = video_tx.send(VideoCmd::LocalKey { id: msg["id"].as_str().unwrap_or("").into(), key: b64(&msg["key"]) });
            }
            "remote_key" => {
                let _ = video_tx.send(VideoCmd::RemoteKey {
                    user_id: msg["user_id"].as_i64().unwrap_or(0),
                    id: msg["id"].as_str().unwrap_or("").into(),
                    key: b64(&msg["key"]),
                });
            }
            "vconnect" => {
                let _ = video_tx.send(VideoCmd::Connect {
                    url: msg["url"].as_str().unwrap_or("").to_string(),
                    token: msg["token"].as_str().unwrap_or("").to_string(),
                    my_id: msg["my_id"].as_i64().unwrap_or(0),
                });
            }
            "vdisconnect" => {
                let _ = video_tx.send(VideoCmd::Disconnect);
            }
            "watch" => {
                let _ = video_tx.send(VideoCmd::Watch { user_id: num(&msg["user_id"], 0), on: msg["on"].as_bool().unwrap_or(false) });
            }
            "share" => {
                let rect = &msg["rect"];
                let spec = capture::ShareSpec {
                    kind: msg["kind"].as_str().unwrap_or("monitor").to_string(),
                    ident: msg["ident"].as_u64().or_else(|| msg["ident"].as_str().and_then(|s| s.parse().ok())).unwrap_or(1),
                    rect: (num(&rect[0], 0) as i32, num(&rect[1], 0) as i32, num(&rect[2], 0) as i32, num(&rect[3], 0) as i32),
                    width: (num(&msg["width"], 1920) as u32).max(16) & !1,
                    height: (num(&msg["height"], 1080) as u32).max(16) & !1,
                    fps: num(&msg["fps"], 30).clamp(1, 120) as u32,
                    bitrate: num(&msg["bitrate"], 5_000_000).max(100_000) as u64,
                    encoder: msg["encoder"].as_str().unwrap_or("").to_string(),
                };
                let _ = video_tx.send(VideoCmd::Share(spec));
            }
            "share_stop" => {
                let _ = video_tx.send(VideoCmd::ShareStop);
            }
            "camera" => {
                let spec = camera::CameraSpec {
                    id: msg["id"].as_str().unwrap_or("").to_string(),
                    title: msg["title"].as_str().unwrap_or("").to_string(),
                    max_height: num(&msg["max_height"], 720) as u32,
                    fps: num(&msg["fps"], 30).clamp(1, 60) as u32,
                    bitrate: num(&msg["bitrate"], 1_700_000).max(100_000) as u64,
                };
                let _ = video_tx.send(VideoCmd::Camera(spec));
            }
            "camera_stop" => {
                let _ = video_tx.send(VideoCmd::CameraStop);
            }
            "quit" => break,
            other => log::warn!("Unknown video command {other}"),
        }
    }
    let _ = video_tx.send(VideoCmd::Disconnect);
    std::thread::sleep(Duration::from_millis(300));
    runtime.shutdown_timeout(Duration::from_secs(2));
}

/// `vcvoice --selftest [width height fps]`: checks the video parts on this computer without a call. Captures
/// monitor 1 for 6 s and encodes it on the graphics card (or says why not), then opens the webcam for 3 s.
/// Prints one JSON line with what happened.
fn selftest() {
    use std::sync::atomic::Ordering;
    let args: Vec<u32> = std::env::args().skip(2).filter_map(|a| a.parse().ok()).collect();
    let (width, height, fps) = (*args.first().unwrap_or(&2560), *args.get(1).unwrap_or(&1440), *args.get(2).unwrap_or(&60));
    let mut report = json!({"width": width, "height": height, "fps": fps});
    let source = livekit::webrtc::video_source::native::NativeVideoSource::new_encoded(
        livekit::webrtc::prelude::VideoResolution { width, height },
    );
    let card = cardenc::CardEncoder::start(source, width, height, fps, 12_000_000, Box::new(|reason| {
        log::warn!("card encoder failed: {reason}");
    }));
    match &card {
        Ok(c) => report["encoder"] = json!(c.name()),
        Err(e) => report["encoder_error"] = json!(e),
    }
    let spec = capture::ShareSpec {
        kind: "monitor".into(), ident: 1, rect: (0, 0, 0, 0), width, height, fps, bitrate: 12_000_000, encoder: String::new(),
    };
    let stop = Arc::new(AtomicBool::new(false));
    let counters = Arc::new(capture::CaptureCounters::default());
    {
        let stop = stop.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_secs(6));
            stop.store(true, Ordering::Relaxed);
        });
    }
    let started = std::time::Instant::now();
    let result = capture::run(&spec, stop, counters.clone(), |picture, ts| {
        if let Ok(c) = &card {
            c.push(picture, ts);
        }
    });
    let seconds = started.elapsed().as_secs_f64();
    report["capture_error"] = json!(result.err());
    report["capture_fps"] = json!((counters.handed.load(Ordering::Relaxed) as f64 / seconds * 10.0).round() / 10.0);
    report["source"] = json!(*counters.source_size.lock());
    report["convert_ms"] = json!(counters.grab_us.load(Ordering::Relaxed) as f64 / 1000.0);
    if let Ok(c) = &card {
        report["encoded_fps"] = json!((c.encoded.load(Ordering::Relaxed) as f64 / seconds * 10.0).round() / 10.0);
    }
    drop(card);
    let cam = camera::CameraSpec { id: String::new(), title: String::new(), max_height: 720, fps: 30, bitrate: 1_700_000 };
    let (ready_tx, ready_rx) = std::sync::mpsc::channel();
    let cam_stop = Arc::new(AtomicBool::new(false));
    let frames = Arc::new(std::sync::atomic::AtomicU32::new(0));
    let thread = {
        let cam_stop = cam_stop.clone();
        let frames = frames.clone();
        std::thread::spawn(move || camera::run(&cam, cam_stop, ready_tx, |_, _| {
            frames.fetch_add(1, Ordering::Relaxed);
        }))
    };
    match ready_rx.recv_timeout(Duration::from_secs(10)) {
        Ok(Ok(size)) => {
            std::thread::sleep(Duration::from_secs(3));
            report["camera"] = json!({"size": size, "fps": frames.load(Ordering::Relaxed) as f64 / 3.0});
        }
        Ok(Err(e)) => report["camera"] = json!({"error": e}),
        Err(_) => report["camera"] = json!({"error": "no answer in 10 s"}),
    }
    cam_stop.store(true, Ordering::Relaxed);
    let _ = thread.join();
    println!("{report}");
}
