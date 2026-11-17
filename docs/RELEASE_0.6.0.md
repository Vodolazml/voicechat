# VoiceChat 0.6.0 (client only)

Goal: the app must not cost games fps, even on weak PCs. Until now every 10 ms of voice went through
Python (~800-900 wakeups a second in a call). Voice now runs in a native engine.

## Native voice engine (vcvoice.exe, Rust)
- Microphone, WebRTC echo/noise processing, RNNoise ("strong"), voice gate with the threshold, our
  leveler and -3 dBFS limiter, the LiveKit voice connection with E2EE, mixing and the speakers: all in
  one native process. The app only sends commands (join, mute, keys, settings) and gets events.
- Measured on PC1 with strong NS + echo cancellation: 5% of one core, 30 MB memory, 11 threads.
- Its own LiveKit participant "<id>-audio"; the Python room carries screen share and camera only.
  Older clients still hear and are heard (subscribes to "uN" and "N-audio" mics).
- Engine crash: restarted with its full state up to 5 times, then a notice.
- UI sounds in a call are mixed by the engine (no second output stream).
- Shortcut keys and push-to-talk are read by the engine (no Python polling every 15 ms).
- Fallback: without vcvoice.exe (or VOICECHAT_NATIVE_VOICE=0) the old Python voice path is used.

## Other fps fixes
- In a call the main window rendered ~200 fps: the hidden home screen's logo animation kept running.
- Overlay in "only who speaks" mode hides its window entirely while nobody talks (a window above a
  borderless game takes away its fast presentation path).
- The overlay is re-raised only if another topmost window got above it (was every second).

## Settings
- "Порог срабатывания микрофона" (auto / manual) moved right under the mic meter; the manual threshold
  works with any noise suppression, including "Стандартное". The meter is taller, with the marker.

## Build
- `native/vcvoice`: `cargo build --release` (Windows: static CRT via `.cargo/config.toml`).
- The spec bundles `vcvoice.exe` from `VCVOICE_EXE`, `native/vcvoice/target/release/` or
  `C:\vcb\vcvoice-target\release\`; it warns if missing.
