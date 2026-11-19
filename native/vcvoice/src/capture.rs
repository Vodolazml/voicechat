//! Screen and window capture for a screen share, with WebRTC's own capturer (Windows Graphics Capture,
//! Desktop Duplication as a fallback): no extra process, no Python. Each picture is turned into I420 at
//! the size we send with libyuv, and handed on: to the encoder and, at most 30 times a second, to our own
//! preview in the window.

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use livekit::webrtc::desktop_capturer::{
    CaptureError, DesktopCaptureSourceType, DesktopCapturer, DesktopCapturerOptions, DesktopFrame,
};
use livekit::webrtc::native::yuv_helper;
use livekit::webrtc::video_frame::{I420Buffer, VideoBuffer};
use parking_lot::Mutex;

#[derive(Clone, Debug)]
pub struct ShareSpec {
    /// "monitor" or "window".
    pub kind: String,
    /// The window handle, or the monitor's number (1-based, as the app lists them).
    pub ident: u64,
    /// The monitor's rectangle in physical pixels: picks the same screen as the app's list.
    pub rect: (i32, i32, i32, i32),
    pub width: u32,
    pub height: u32,
    pub fps: u32,
    pub bitrate: u64,
    /// "" picks the card's encoder when there is one, "SOFTWARE" keeps the processor.
    pub encoder: String,
}

/// Counters the share's statistics read once a second.
#[derive(Default)]
pub struct CaptureCounters {
    pub captured: AtomicU32,
    pub handed: AtomicU32,
    /// Hand over at most this many frames a second while the encoder cannot keep up (0 = every frame).
    pub frame_limit: AtomicU32,
    pub source_size: Mutex<(u32, u32)>,
    pub grab_us: AtomicU32,
}

/// Runs on its own thread until `stop` is set. `deliver` gets each picture at the size we send.
pub fn run(
    spec: &ShareSpec,
    stop: Arc<AtomicBool>,
    counters: Arc<CaptureCounters>,
    mut deliver: impl FnMut(I420Buffer, i64),
) -> Result<(), String> {
    let window = spec.kind == "window";
    let mut options = DesktopCapturerOptions::new(if window {
        DesktopCaptureSourceType::Window
    } else {
        DesktopCaptureSourceType::Screen
    });
    options.set_include_cursor(true);
    let mut capturer = DesktopCapturer::new(options).ok_or("Захват экрана недоступен")?;
    let sources = capturer.get_source_list();
    let wanted = if window { Some(spec.ident) } else { screen_id(spec) };
    let source = match wanted.and_then(|id| sources.iter().find(|s| s.id() == id)) {
        Some(s) => s.clone(),
        None if window => return Err("Окно закрыто или его нельзя показать".into()),
        None => {
            // Not found by position: the n-th screen in WebRTC's own order.
            let index = (spec.ident as usize).saturating_sub(1);
            sources.get(index).or_else(|| sources.first()).cloned().ok_or("Не найден экран для демонстрации")?
        }
    };
    log::info!(
        "Screen capture: {} {} (source {}), {}x{} at {} fps",
        spec.kind,
        spec.ident,
        source.id(),
        spec.width,
        spec.height,
        spec.fps
    );
    let latest: Arc<Mutex<Option<I420Buffer>>> = Arc::new(Mutex::new(None));
    let failed = Arc::new(AtomicBool::new(false));
    {
        let latest = latest.clone();
        let failed = failed.clone();
        let counters = counters.clone();
        let (out_w, out_h) = (spec.width, spec.height);
        capturer.start_capture(Some(source), move |result: Result<DesktopFrame, CaptureError>| match result {
            Ok(frame) => {
                let started = Instant::now();
                if let Some(picture) = to_i420(&frame, out_w, out_h) {
                    *counters.source_size.lock() = (frame.width() as u32, frame.height() as u32);
                    *latest.lock() = Some(picture);
                    counters.captured.fetch_add(1, Ordering::Relaxed);
                    let spent = started.elapsed().as_micros() as u32;
                    let old = counters.grab_us.load(Ordering::Relaxed);
                    counters.grab_us.store((old * 9 + spent) / 10, Ordering::Relaxed);
                }
            }
            Err(CaptureError::Permanent) => failed.store(true, Ordering::Relaxed),
            Err(CaptureError::Temporary) => {} // minimised window, a mode change: keep the last picture
        });
    }
    let started = Instant::now();
    let mut due = Instant::now();
    while !stop.load(Ordering::Relaxed) {
        let limit = counters.frame_limit.load(Ordering::Relaxed);
        let fps = if limit > 0 { limit.min(spec.fps) } else { spec.fps }.max(1);
        let interval = Duration::from_secs_f64(1.0 / fps as f64);
        // WebRTC's capturers hand back the last picture again when nothing changed on screen.
        capturer.capture_frame();
        if failed.load(Ordering::Relaxed) {
            return Err("Захват экрана прервался".into());
        }
        let picture = latest.lock().take();
        if let Some(picture) = picture {
            deliver(picture, started.elapsed().as_micros() as i64);
            counters.handed.fetch_add(1, Ordering::Relaxed);
        }
        due += interval;
        let now = Instant::now();
        if due > now {
            std::thread::sleep(due - now);
        } else if now - due > interval * 4 {
            due = now; // fell far behind (the machine was busy): start counting again
        }
    }
    Ok(())
}

/// The monitor's id in WebRTC's list. WebRTC numbers screens by their display device index; we find the
/// device whose desktop position and size match the rectangle the app chose.
#[cfg(windows)]
fn screen_id(spec: &ShareSpec) -> Option<u64> {
    use windows_sys::Win32::Graphics::Gdi::{
        EnumDisplayDevicesW, EnumDisplaySettingsW, DEVMODEW, DISPLAY_DEVICEW, DISPLAY_DEVICE_ACTIVE,
        ENUM_CURRENT_SETTINGS,
    };
    unsafe {
        for index in 0..32u32 {
            let mut device: DISPLAY_DEVICEW = std::mem::zeroed();
            device.cb = std::mem::size_of::<DISPLAY_DEVICEW>() as u32;
            if EnumDisplayDevicesW(std::ptr::null(), index, &mut device, 0) == 0 {
                break;
            }
            if device.StateFlags & DISPLAY_DEVICE_ACTIVE == 0 {
                continue;
            }
            let mut mode: DEVMODEW = std::mem::zeroed();
            mode.dmSize = std::mem::size_of::<DEVMODEW>() as u16;
            if EnumDisplaySettingsW(device.DeviceName.as_ptr(), ENUM_CURRENT_SETTINGS, &mut mode) == 0 {
                continue;
            }
            let pos = mode.Anonymous1.Anonymous2.dmPosition;
            let (l, t, w, h) = spec.rect;
            if pos.x == l && pos.y == t && mode.dmPelsWidth as i32 == w && mode.dmPelsHeight as i32 == h {
                return Some(index as u64);
            }
        }
    }
    None
}

#[cfg(not(windows))]
fn screen_id(_spec: &ShareSpec) -> Option<u64> {
    None
}

/// BGRA picture -> I420 at the size we send. A window whose shape no longer matches keeps its
/// proportions, with black bars around it.
pub fn to_i420(frame: &DesktopFrame, out_w: u32, out_h: u32) -> Option<I420Buffer> {
    let (w, h) = (frame.width(), frame.height());
    if w < 2 || h < 2 {
        return None;
    }
    let data = frame.data();
    let stride = frame.stride();
    if data.len() < (stride as usize) * (h as usize) {
        return None;
    }
    let mut full = I420Buffer::new(w as u32, h as u32);
    {
        let (sy, su, sv) = full.strides();
        let (y, u, v) = full.data_mut();
        // libyuv's "ARGB" is B, G, R, A in memory: what Windows captures.
        yuv_helper::argb_to_i420(data, stride, y, sy, u, su, v, sv, w, h);
    }
    Some(fit(full, out_w, out_h))
}

pub fn fit(mut picture: I420Buffer, out_w: u32, out_h: u32) -> I420Buffer {
    let (w, h) = (picture.width(), picture.height());
    if (w, h) == (out_w, out_h) {
        return picture;
    }
    let same_shape = ((w as f64 / h as f64) - (out_w as f64 / out_h as f64)).abs() < 0.01;
    if same_shape {
        return picture.scale(out_w as i32, out_h as i32);
    }
    let scale = (out_w as f64 / w as f64).min(out_h as f64 / h as f64);
    let fit_w = (((w as f64 * scale) as u32).max(2) & !1).min(out_w);
    let fit_h = (((h as f64 * scale) as u32).max(2) & !1).min(out_h);
    let scaled = if (fit_w, fit_h) == (w, h) { picture } else { picture.scale(fit_w as i32, fit_h as i32) };
    let mut canvas = I420Buffer::new_black(out_w, out_h);
    let x = ((out_w - fit_w) / 2) & !1;
    let y = ((out_h - fit_h) / 2) & !1;
    paste(&mut canvas, &scaled, x, y);
    canvas
}

fn paste(canvas: &mut I420Buffer, picture: &I420Buffer, x: u32, y: u32) {
    let (dsy, dsu, dsv) = canvas.strides();
    let (ssy, ssu, ssv) = picture.strides();
    let (pw, ph) = (picture.width() as usize, picture.height() as usize);
    let (pcw, pch) = ((pw + 1) / 2, (ph + 1) / 2);
    let (sy, su, sv) = picture.data();
    let (dy, du, dv) = canvas.data_mut();
    let copy = |dst: &mut [u8], ds: u32, src: &[u8], ss: u32, ox: usize, oy: usize, w: usize, h: usize| {
        for row in 0..h {
            let d = (oy + row) * ds as usize + ox;
            let s = row * ss as usize;
            if d + w <= dst.len() && s + w <= src.len() {
                dst[d..d + w].copy_from_slice(&src[s..s + w]);
            }
        }
    };
    copy(dy, dsy, sy, ssy, x as usize, y as usize, pw, ph);
    copy(du, dsu, su, ssu, x as usize / 2, y as usize / 2, pcw, pch);
    copy(dv, dsv, sv, ssv, x as usize / 2, y as usize / 2, pcw, pch);
}

/// Our own preview is at most Full HD wide: a smaller copy of a bigger picture.
pub fn preview_size(width: u32, height: u32) -> Option<(u32, u32)> {
    const MAX_W: u32 = 1920;
    (width > MAX_W).then(|| (MAX_W, (((height as u64 * MAX_W as u64 / width as u64) as u32) & !1).max(2)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_window_of_another_shape_gets_black_bars() {
        let picture = I420Buffer::new(400, 400);
        let out = fit(picture, 640, 360);
        assert_eq!((out.width(), out.height()), (640, 360));
        let (y, _, _) = out.data();
        assert_eq!(y[0], 0, "left bar is black");
    }
}
