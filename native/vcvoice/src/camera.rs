//! The webcam, read with Media Foundation's source reader (the same system part Qt used, without Qt and
//! Python in between). The best format up to 720p that runs at 24 frames a second or more is chosen; the
//! reader decodes MJPEG cameras and hands out NV12, which becomes I420 for WebRTC's encoder.

use std::sync::atomic::AtomicBool;
#[cfg(windows)]
use std::sync::atomic::Ordering;
use std::sync::mpsc::Sender;
use std::sync::Arc;

use livekit::webrtc::video_frame::I420Buffer;

#[derive(Clone, Debug)]
pub struct CameraSpec {
    /// The device's symbolic link as the app lists it, and its name (used if the link changed).
    pub id: String,
    pub title: String,
    pub max_height: u32,
    pub fps: u32,
    pub bitrate: u64,
}

/// Runs until `stop` is set. Sends the opened size through `ready` (or why it could not open), then
/// hands each picture to `deliver`.
pub fn run(
    spec: &CameraSpec,
    stop: Arc<AtomicBool>,
    ready: Sender<Result<(u32, u32), String>>,
    deliver: impl FnMut(I420Buffer, i64),
) -> Result<(), String> {
    imp::run(spec, stop, ready, deliver)
}

#[cfg(not(windows))]
mod imp {
    use super::*;

    pub fn run(
        _spec: &CameraSpec,
        _stop: Arc<AtomicBool>,
        ready: Sender<Result<(u32, u32), String>>,
        _deliver: impl FnMut(I420Buffer, i64),
    ) -> Result<(), String> {
        let _ = ready.send(Err("Камера не поддерживается на этой системе".into()));
        Ok(())
    }
}

#[cfg(windows)]
mod imp {
    use super::*;
    use std::time::{Duration, Instant};

    use livekit::webrtc::native::yuv_helper;
    use windows::core::{GUID, PWSTR};
    use windows::Win32::Media::MediaFoundation::*;
    use windows::Win32::System::Com::{CoInitializeEx, CoTaskMemFree, CoUninitialize, COINIT_MULTITHREADED};

    const FIRST_VIDEO: u32 = MF_SOURCE_READER_FIRST_VIDEO_STREAM.0 as u32;

    fn text(activate: &IMFActivate, key: &GUID) -> String {
        unsafe {
            let mut value = PWSTR::null();
            let mut length = 0u32;
            if activate.GetAllocatedString(key, &mut value, &mut length).is_ok() && !value.is_null() {
                let s = value.to_string().unwrap_or_default();
                CoTaskMemFree(Some(value.0 as *const _));
                return s;
            }
        }
        String::new()
    }

    fn unpack(value: u64) -> (u32, u32) {
        ((value >> 32) as u32, value as u32)
    }

    pub fn run(
        spec: &CameraSpec,
        stop: Arc<AtomicBool>,
        ready: Sender<Result<(u32, u32), String>>,
        mut deliver: impl FnMut(I420Buffer, i64),
    ) -> Result<(), String> {
        unsafe {
            let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
            if let Err(e) = MFStartup(MF_VERSION, MFSTARTUP_FULL) {
                let _ = ready.send(Err(format!("Камера недоступна ({e})")));
                CoUninitialize();
                return Ok(());
            }
        }
        let mut ready = Some(ready);
        let result = read(spec, &stop, &mut ready, &mut deliver);
        if let (Err(e), Some(ready)) = (&result, ready.take()) {
            // Failed before it opened: the caller hears it as a refusal, not as a failure mid-call.
            let _ = ready.send(Err(e.clone()));
        }
        unsafe {
            let _ = MFShutdown();
            CoUninitialize();
        }
        if ready.is_none() {
            result
        } else {
            Ok(())
        }
    }

    fn read(
        spec: &CameraSpec,
        stop: &AtomicBool,
        ready: &mut Option<Sender<Result<(u32, u32), String>>>,
        deliver: &mut impl FnMut(I420Buffer, i64),
    ) -> Result<(), String> {
        unsafe {
            let mut attributes: Option<IMFAttributes> = None;
            MFCreateAttributes(&mut attributes, 1).map_err(|e| e.to_string())?;
            let attributes = attributes.ok_or("no attributes")?;
            attributes
                .SetGUID(&MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE, &MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID)
                .map_err(|e| e.to_string())?;
            let mut list: *mut Option<IMFActivate> = std::ptr::null_mut();
            let mut count = 0u32;
            MFEnumDeviceSources(&attributes, &mut list, &mut count).map_err(|e| format!("Камеры не найдены ({e})"))?;
            let devices: Vec<IMFActivate> =
                (0..count as usize).filter_map(|i| (*list.add(i)).take()).collect();
            if !list.is_null() {
                CoTaskMemFree(Some(list as *const _));
            }
            if devices.is_empty() {
                return Err("Камера не найдена".into());
            }
            let named: Vec<(String, String)> = devices
                .iter()
                .map(|d| {
                    (
                        text(d, &MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_SYMBOLIC_LINK),
                        text(d, &MF_DEVSOURCE_ATTRIBUTE_FRIENDLY_NAME),
                    )
                })
                .collect();
            let index = named
                .iter()
                .position(|(link, _)| !spec.id.is_empty() && link.eq_ignore_ascii_case(&spec.id))
                .or_else(|| named.iter().position(|(_, name)| !spec.title.is_empty() && name == &spec.title))
                .unwrap_or(0);
            log::info!("Camera: {}", named[index].1);
            let source: IMFMediaSource =
                devices[index].ActivateObject().map_err(|e| format!("Камера недоступна ({e})"))?;

            let mut reader_attributes: Option<IMFAttributes> = None;
            MFCreateAttributes(&mut reader_attributes, 1).map_err(|e| e.to_string())?;
            let reader_attributes = reader_attributes.ok_or("no attributes")?;
            // Lets the reader decode MJPEG and convert to NV12 for us.
            let _ = reader_attributes.SetUINT32(&MF_SOURCE_READER_ENABLE_ADVANCED_VIDEO_PROCESSING, 1);
            let reader = MFCreateSourceReaderFromMediaSource(&source, &reader_attributes)
                .map_err(|e| format!("Камера недоступна ({e})"))?;

            // The best native format up to the height limit at 24 fps or more.
            let mut best: Option<(IMFMediaType, u32, u32, u32)> = None;
            for i in 0.. {
                let Ok(native) = reader.GetNativeMediaType(FIRST_VIDEO, i) else { break };
                let (w, h) = unpack(native.GetUINT64(&MF_MT_FRAME_SIZE).unwrap_or(0));
                let (num, den) = unpack(native.GetUINT64(&MF_MT_FRAME_RATE).unwrap_or(0));
                let fps = if den > 0 { num / den } else { 0 };
                if h == 0 || h > spec.max_height || fps < 24 {
                    continue;
                }
                let score = (h, fps.min(spec.fps));
                if best.as_ref().map(|(_, _, bh, bf)| score > (*bh, (*bf).min(spec.fps))).unwrap_or(true) {
                    best = Some((native, w, h, fps));
                }
            }
            let (width, height, fps) = match best {
                Some((native, w, h, fps)) => {
                    // Choosing a native type switches the camera itself to it.
                    let _ = reader.SetCurrentMediaType(FIRST_VIDEO, None, &native);
                    (w, h, fps)
                }
                None => (1280, 720, 30),
            };
            let output = MFCreateMediaType().map_err(|e| e.to_string())?;
            output.SetGUID(&MF_MT_MAJOR_TYPE, &MFMediaType_Video).map_err(|e| e.to_string())?;
            output.SetGUID(&MF_MT_SUBTYPE, &MFVideoFormat_NV12).map_err(|e| e.to_string())?;
            output.SetUINT64(&MF_MT_FRAME_SIZE, ((width as u64) << 32) | height as u64).map_err(|e| e.to_string())?;
            reader
                .SetCurrentMediaType(FIRST_VIDEO, None, &output)
                .map_err(|e| format!("Камера не отдаёт подходящий формат ({e})"))?;
            let current = reader.GetCurrentMediaType(FIRST_VIDEO).map_err(|e| e.to_string())?;
            let (width, height) = unpack(current.GetUINT64(&MF_MT_FRAME_SIZE).unwrap_or(((width as u64) << 32) | height as u64));
            let (out_w, out_h) = (width & !1, height & !1);
            if let Some(ready) = ready.take() {
                let _ = ready.send(Ok((out_w, out_h)));
            }
            log::info!("Camera opened: {width}x{height}, {fps} fps native");

            let started = Instant::now();
            let interval = Duration::from_secs_f64(1.0 / spec.fps.max(1) as f64);
            let mut due = Instant::now();
            while !stop.load(Ordering::Relaxed) {
                let mut flags = 0u32;
                let mut sample: Option<IMFSample> = None;
                reader
                    .ReadSample(FIRST_VIDEO, 0, None, Some(&mut flags), None, Some(&mut sample))
                    .map_err(|e| format!("Камера перестала отвечать ({e})"))?;
                if flags & (MF_SOURCE_READERF_ERROR.0 as u32 | MF_SOURCE_READERF_ENDOFSTREAM.0 as u32) != 0 {
                    return Err("Камера отключилась".into());
                }
                let Some(sample) = sample else { continue };
                let now = Instant::now();
                if now < due {
                    continue; // the camera runs faster than we send
                }
                due = if due + interval < now { now + interval } else { due + interval };
                let buffer = sample.ConvertToContiguousBuffer().map_err(|e| e.to_string())?;
                let mut data: *mut u8 = std::ptr::null_mut();
                let mut length = 0u32;
                buffer.Lock(&mut data, None, Some(&mut length)).map_err(|e| e.to_string())?;
                let bytes = std::slice::from_raw_parts(data, length as usize);
                let stride = width as usize;
                let y_size = stride * height as usize;
                if bytes.len() >= y_size + y_size / 2 {
                    let mut picture = I420Buffer::new(out_w, out_h);
                    let (sy, su, sv) = picture.strides();
                    let (y, u, v) = picture.data_mut();
                    yuv_helper::nv12_to_i420(
                        &bytes[..y_size],
                        stride as u32,
                        &bytes[y_size..],
                        stride as u32,
                        y,
                        sy,
                        u,
                        su,
                        v,
                        sv,
                        out_w as i32,
                        out_h as i32,
                    );
                    let _ = buffer.Unlock();
                    deliver(picture, started.elapsed().as_micros() as i64);
                } else {
                    let _ = buffer.Unlock();
                }
            }
            Ok(())
        }
    }
}
