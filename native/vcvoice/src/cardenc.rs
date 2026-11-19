//! H.264 encoded by the graphics card's own video block (NVIDIA NVENC, AMD VCE/VCN, Intel Quick Sync),
//! through the Media Foundation hardware encoder each driver installs. WebRTC's Windows build has no
//! hardware encoder: it encoded every frame on the processor with OpenH264, and next to a game fell from
//! 60 to 8 fps at 1440p (2026-11-13). Here the card encodes and WebRTC only sends what comes out.
//!
//! One thread owns the encoder (Media Foundation objects stay on the thread that made them). Pictures
//! come in through a one-picture slot: a newer one replaces one still waiting, so the encoder is never
//! behind by more than a frame. WebRTC's requests are followed: a key frame for a new viewer, a lower
//! bitrate when the network cannot carry more.

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::Arc;

use livekit::webrtc::video_frame::I420Buffer;
use livekit::webrtc::video_source::native::NativeVideoSource;
use parking_lot::{Condvar, Mutex};

pub type OnFailure = Box<dyn FnOnce(String) + Send>;

struct Slot {
    picture: Mutex<Option<(I420Buffer, i64)>>,
    wake: Condvar,
}

pub struct CardEncoder {
    slot: Arc<Slot>,
    stop: Arc<AtomicBool>,
    pub encoded: Arc<AtomicU32>,
    name: String,
    thread: Option<std::thread::JoinHandle<()>>,
}

impl CardEncoder {
    /// Opens the card's encoder (on its own thread) and returns once it is ready, or why it is not.
    pub fn start(
        source: NativeVideoSource,
        width: u32,
        height: u32,
        fps: u32,
        bitrate: u64,
        on_failure: OnFailure,
    ) -> Result<Self, String> {
        if std::env::var("VOICECHAT_VIDEO_ENCODER").map(|v| v.eq_ignore_ascii_case("SOFTWARE")).unwrap_or(false) {
            return Err("turned off".into());
        }
        let slot = Arc::new(Slot { picture: Mutex::new(None), wake: Condvar::new() });
        let stop = Arc::new(AtomicBool::new(false));
        let encoded = Arc::new(AtomicU32::new(0));
        let (ready_tx, ready_rx) = std::sync::mpsc::channel::<Result<String, String>>();
        let thread = {
            let slot = slot.clone();
            let stop = stop.clone();
            let encoded = encoded.clone();
            std::thread::Builder::new()
                .name("card-encoder".into())
                .spawn(move || imp::run(source, width, height, fps, bitrate, slot_api(slot), stop, encoded, ready_tx, on_failure))
                .map_err(|e| e.to_string())?
        };
        match ready_rx.recv() {
            Ok(Ok(name)) => {
                log::info!("Card encoder {name}: {width}x{height} at {fps} fps, {} kbps", bitrate / 1000);
                Ok(Self { slot, stop, encoded, name, thread: Some(thread) })
            }
            Ok(Err(e)) => {
                let _ = thread.join();
                Err(e)
            }
            Err(_) => {
                let _ = thread.join();
                Err("the encoder thread ended".into())
            }
        }
    }

    pub fn name(&self) -> &str {
        &self.name
    }

    /// A picture to encode. Replaces one that is still waiting.
    pub fn push(&self, picture: I420Buffer, timestamp_us: i64) {
        *self.slot.picture.lock() = Some((picture, timestamp_us));
        self.slot.wake.notify_one();
    }
}

impl Drop for CardEncoder {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        self.slot.wake.notify_all();
        // Not joined: a driver that stops answering must not hang the share's end. The thread sees the
        // flag within 200 ms and closes the encoder itself.
        drop(self.thread.take());
    }
}

/// What the encoder thread sees of the slot: wait for the next picture (or a stop).
pub struct SlotApi(Arc<Slot>);

fn slot_api(slot: Arc<Slot>) -> SlotApi {
    SlotApi(slot)
}

impl SlotApi {
    fn take(&self, stop: &AtomicBool, wait_ms: u64) -> Option<(I420Buffer, i64)> {
        let mut guard = self.0.picture.lock();
        if guard.is_none() && !stop.load(Ordering::Relaxed) {
            self.0.wake.wait_for(&mut guard, std::time::Duration::from_millis(wait_ms));
        }
        guard.take()
    }
}

/// Annex B NAL unit types in an access unit.
#[allow(dead_code)]
fn nal_types(data: &[u8]) -> Vec<u8> {
    let mut types = Vec::new();
    let mut i = 0;
    while i + 3 < data.len() {
        if data[i] == 0 && data[i + 1] == 0 && (data[i + 2] == 1 || (data[i + 2] == 0 && data[i + 3] == 1)) {
            let start = if data[i + 2] == 1 { i + 3 } else { i + 4 };
            if start < data.len() {
                types.push(data[start] & 0x1f);
            }
            i = start;
        } else {
            i += 1;
        }
    }
    types
}

#[cfg(not(windows))]
mod imp {
    use super::*;

    #[allow(clippy::too_many_arguments)]
    pub fn run(
        _source: NativeVideoSource,
        _width: u32,
        _height: u32,
        _fps: u32,
        _bitrate: u64,
        _slot: SlotApi,
        _stop: Arc<AtomicBool>,
        _encoded: Arc<AtomicU32>,
        ready: std::sync::mpsc::Sender<Result<String, String>>,
        _on_failure: OnFailure,
    ) {
        let _ = ready.send(Err("no card encoder on this system".into()));
    }
}

#[cfg(windows)]
mod imp {
    use super::*;
    use std::mem::ManuallyDrop;

    use livekit::webrtc::native::yuv_helper;
    use livekit::webrtc::prelude::VideoResolution;
    use livekit::webrtc::video_frame::{EncodedFrameType, EncodedVideoCodec, EncodedVideoFrame};
    use windows::core::{Interface, GUID, PWSTR};
    use windows::Win32::Media::MediaFoundation::*;
    use windows::Win32::System::Com::{CoInitializeEx, CoTaskMemFree, CoUninitialize, COINIT_MULTITHREADED};
    use windows::Win32::System::Variant::{VARIANT, VARIANT_0, VARIANT_0_0, VARIANT_0_0_0, VT_BOOL, VT_UI4};

    const HNS_PER_US: i64 = 10;

    fn u32_variant(value: u32) -> VARIANT {
        VARIANT {
            Anonymous: VARIANT_0 {
                Anonymous: ManuallyDrop::new(VARIANT_0_0 {
                    vt: VT_UI4,
                    wReserved1: 0,
                    wReserved2: 0,
                    wReserved3: 0,
                    Anonymous: VARIANT_0_0_0 { ulVal: value },
                }),
            },
        }
    }

    fn bool_variant(value: bool) -> VARIANT {
        VARIANT {
            Anonymous: VARIANT_0 {
                Anonymous: ManuallyDrop::new(VARIANT_0_0 {
                    vt: VT_BOOL,
                    wReserved1: 0,
                    wReserved2: 0,
                    wReserved3: 0,
                    Anonymous: VARIANT_0_0_0 {
                        boolVal: windows::Win32::Foundation::VARIANT_BOOL(if value { -1 } else { 0 }),
                    },
                }),
            },
        }
    }

    fn pack(high: u32, low: u32) -> u64 {
        ((high as u64) << 32) | low as u64
    }

    struct Encoder {
        transform: IMFTransform,
        events: IMFMediaEventGenerator,
        codec: Option<ICodecAPI>,
        input_id: u32,
        output_id: u32,
        sequence_header: Vec<u8>,
        width: u32,
        height: u32,
        fps: u32,
    }

    fn set_codec(codec: &Option<ICodecAPI>, api: &GUID, value: VARIANT) -> bool {
        match codec {
            Some(c) => unsafe { c.SetValue(api, &value).is_ok() },
            None => false,
        }
    }

    fn friendly_name(activate: &IMFActivate) -> String {
        unsafe {
            let mut text = PWSTR::null();
            let mut length = 0u32;
            if activate.GetAllocatedString(&MFT_FRIENDLY_NAME_Attribute, &mut text, &mut length).is_ok() && !text.is_null() {
                let name = text.to_string().unwrap_or_default();
                CoTaskMemFree(Some(text.0 as *const _));
                return name;
            }
        }
        "Media Foundation".into()
    }

    impl Encoder {
        fn open(width: u32, height: u32, fps: u32, bitrate: u64) -> Result<(Self, String), String> {
            unsafe {
                let input = MFT_REGISTER_TYPE_INFO { guidMajorType: MFMediaType_Video, guidSubtype: MFVideoFormat_NV12 };
                let output = MFT_REGISTER_TYPE_INFO { guidMajorType: MFMediaType_Video, guidSubtype: MFVideoFormat_H264 };
                let mut list: *mut Option<IMFActivate> = std::ptr::null_mut();
                let mut count = 0u32;
                MFTEnumEx(
                    MFT_CATEGORY_VIDEO_ENCODER,
                    MFT_ENUM_FLAG_HARDWARE | MFT_ENUM_FLAG_SORTANDFILTER,
                    Some(&input),
                    Some(&output),
                    &mut list,
                    &mut count,
                )
                .map_err(|e| format!("MFTEnumEx: {e}"))?;
                if count == 0 || list.is_null() {
                    return Err("no hardware H.264 encoder".into());
                }
                let activates: Vec<Option<IMFActivate>> = (0..count as usize).map(|i| (*list.add(i)).take()).collect();
                CoTaskMemFree(Some(list as *const _));
                let mut last_error = String::new();
                for activate in activates.into_iter().flatten() {
                    let name = friendly_name(&activate);
                    match Self::open_one(&activate, width, height, fps, bitrate) {
                        Ok(encoder) => return Ok((encoder, name)),
                        Err(e) => {
                            log::info!("Card encoder {name} could not be opened: {e}");
                            last_error = e;
                            let _ = activate.ShutdownObject();
                        }
                    }
                }
                Err(last_error)
            }
        }

        unsafe fn open_one(activate: &IMFActivate, width: u32, height: u32, fps: u32, bitrate: u64) -> Result<Self, String> {
            let transform: IMFTransform = activate.ActivateObject().map_err(|e| format!("activate: {e}"))?;
            if let Ok(attributes) = transform.GetAttributes() {
                // Hardware encoders are asynchronous: they must be unlocked before use.
                let _ = attributes.SetUINT32(&MF_TRANSFORM_ASYNC_UNLOCK, 1);
                let _ = attributes.SetUINT32(&MF_LOW_LATENCY, 1);
            }
            let events: IMFMediaEventGenerator = transform.cast().map_err(|e| format!("not asynchronous: {e}"))?;
            let (mut input_id, mut output_id) = (0u32, 0u32);
            let mut ins = [0u32; 1];
            let mut outs = [0u32; 1];
            if transform.GetStreamIDs(&mut ins, &mut outs).is_ok() {
                input_id = ins[0];
                output_id = outs[0];
            }
            let codec: Option<ICodecAPI> = transform.cast().ok();
            // Constant bitrate, low latency, no B-frames, a key frame every 4 s (viewers ask for one sooner).
            set_codec(&codec, &CODECAPI_AVEncCommonRateControlMode, u32_variant(eAVEncCommonRateControlMode_CBR.0 as u32));
            set_codec(&codec, &CODECAPI_AVEncCommonMeanBitRate, u32_variant(bitrate as u32));
            set_codec(&codec, &CODECAPI_AVLowLatencyMode, bool_variant(true));
            set_codec(&codec, &CODECAPI_AVEncMPVDefaultBPictureCount, u32_variant(0));
            set_codec(&codec, &CODECAPI_AVEncMPVGOPSize, u32_variant(fps * 4));

            let out_type = MFCreateMediaType().map_err(|e| e.to_string())?;
            out_type.SetGUID(&MF_MT_MAJOR_TYPE, &MFMediaType_Video).map_err(|e| e.to_string())?;
            out_type.SetGUID(&MF_MT_SUBTYPE, &MFVideoFormat_H264).map_err(|e| e.to_string())?;
            out_type.SetUINT32(&MF_MT_AVG_BITRATE, bitrate as u32).map_err(|e| e.to_string())?;
            out_type.SetUINT64(&MF_MT_FRAME_SIZE, pack(width, height)).map_err(|e| e.to_string())?;
            out_type.SetUINT64(&MF_MT_FRAME_RATE, pack(fps, 1)).map_err(|e| e.to_string())?;
            out_type.SetUINT64(&MF_MT_PIXEL_ASPECT_RATIO, pack(1, 1)).map_err(|e| e.to_string())?;
            out_type.SetUINT32(&MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive.0 as u32).map_err(|e| e.to_string())?;
            out_type.SetUINT32(&MF_MT_MPEG2_PROFILE, eAVEncH264VProfile_High.0 as u32).map_err(|e| e.to_string())?;
            transform.SetOutputType(output_id, &out_type, 0).map_err(|e| format!("output type: {e}"))?;

            let in_type = MFCreateMediaType().map_err(|e| e.to_string())?;
            in_type.SetGUID(&MF_MT_MAJOR_TYPE, &MFMediaType_Video).map_err(|e| e.to_string())?;
            in_type.SetGUID(&MF_MT_SUBTYPE, &MFVideoFormat_NV12).map_err(|e| e.to_string())?;
            in_type.SetUINT64(&MF_MT_FRAME_SIZE, pack(width, height)).map_err(|e| e.to_string())?;
            in_type.SetUINT64(&MF_MT_FRAME_RATE, pack(fps, 1)).map_err(|e| e.to_string())?;
            in_type.SetUINT64(&MF_MT_PIXEL_ASPECT_RATIO, pack(1, 1)).map_err(|e| e.to_string())?;
            in_type.SetUINT32(&MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive.0 as u32).map_err(|e| e.to_string())?;
            transform.SetInputType(input_id, &in_type, 0).map_err(|e| format!("input type: {e}"))?;

            let mut encoder = Self { transform, events, codec, input_id, output_id, sequence_header: Vec::new(), width, height, fps };
            encoder.read_sequence_header();
            encoder.transform.ProcessMessage(MFT_MESSAGE_COMMAND_FLUSH, 0).map_err(|e| e.to_string())?;
            encoder.transform.ProcessMessage(MFT_MESSAGE_NOTIFY_BEGIN_STREAMING, 0).map_err(|e| e.to_string())?;
            encoder.transform.ProcessMessage(MFT_MESSAGE_NOTIFY_START_OF_STREAM, 0).map_err(|e| e.to_string())?;
            Ok(encoder)
        }

        fn read_sequence_header(&mut self) {
            unsafe {
                if let Ok(current) = self.transform.GetOutputCurrentType(self.output_id) {
                    if let Ok(size) = current.GetBlobSize(&MF_MT_MPEG_SEQUENCE_HEADER) {
                        let mut blob = vec![0u8; size as usize];
                        if current.GetBlob(&MF_MT_MPEG_SEQUENCE_HEADER, &mut blob, None).is_ok() {
                            self.sequence_header = blob;
                        }
                    }
                }
            }
        }

        fn set_bitrate(&self, bitrate: u64) {
            set_codec(&self.codec, &CODECAPI_AVEncCommonMeanBitRate, u32_variant(bitrate as u32));
        }

        fn force_key_frame(&self) {
            set_codec(&self.codec, &CODECAPI_AVEncVideoForceKeyFrame, u32_variant(1));
        }

        fn feed(&self, picture: &I420Buffer, timestamp_us: i64) -> Result<(), String> {
            let (w, h) = (self.width as usize, self.height as usize);
            let size = w * h * 3 / 2;
            unsafe {
                let buffer = MFCreateMemoryBuffer(size as u32).map_err(|e| e.to_string())?;
                let mut data: *mut u8 = std::ptr::null_mut();
                buffer.Lock(&mut data, None, None).map_err(|e| e.to_string())?;
                {
                    let out = std::slice::from_raw_parts_mut(data, size);
                    let (dst_y, dst_uv) = out.split_at_mut(w * h);
                    let (sy, su, sv) = picture.strides();
                    let (y, u, v) = picture.data();
                    yuv_helper::i420_to_nv12(
                        y, sy, u, su, v, sv, dst_y, self.width, dst_uv, self.width, self.width as i32, self.height as i32,
                    );
                }
                buffer.Unlock().map_err(|e| e.to_string())?;
                buffer.SetCurrentLength(size as u32).map_err(|e| e.to_string())?;
                let sample = MFCreateSample().map_err(|e| e.to_string())?;
                sample.AddBuffer(&buffer).map_err(|e| e.to_string())?;
                sample.SetSampleTime(timestamp_us * HNS_PER_US).map_err(|e| e.to_string())?;
                sample.SetSampleDuration(10_000_000 / self.fps.max(1) as i64).map_err(|e| e.to_string())?;
                self.transform.ProcessInput(self.input_id, &sample, 0).map_err(|e| format!("ProcessInput: {e}"))
            }
        }

        /// One encoded access unit: (Annex B bytes, key frame, timestamp in µs).
        fn take_output(&mut self) -> Result<Option<(Vec<u8>, bool, i64)>, String> {
            unsafe {
                let info = self.transform.GetOutputStreamInfo(self.output_id).map_err(|e| e.to_string())?;
                let provides = info.dwFlags & (MFT_OUTPUT_STREAM_PROVIDES_SAMPLES.0 as u32 | MFT_OUTPUT_STREAM_CAN_PROVIDE_SAMPLES.0 as u32) != 0;
                let own = if provides {
                    None
                } else {
                    let sample = MFCreateSample().map_err(|e| e.to_string())?;
                    let buffer = MFCreateMemoryBuffer(info.cbSize.max(1 << 20)).map_err(|e| e.to_string())?;
                    sample.AddBuffer(&buffer).map_err(|e| e.to_string())?;
                    Some(sample)
                };
                let mut buffers = [MFT_OUTPUT_DATA_BUFFER {
                    dwStreamID: self.output_id,
                    pSample: ManuallyDrop::new(own),
                    dwStatus: 0,
                    pEvents: ManuallyDrop::new(None),
                }];
                let mut status = 0u32;
                let result = self.transform.ProcessOutput(0, &mut buffers, &mut status);
                let sample = ManuallyDrop::take(&mut buffers[0].pSample);
                drop(ManuallyDrop::take(&mut buffers[0].pEvents));
                if let Err(e) = result {
                    if e.code() == MF_E_TRANSFORM_STREAM_CHANGE {
                        // The encoder settled its output format: take it as offered.
                        if let Ok(offered) = self.transform.GetOutputAvailableType(self.output_id, 0) {
                            let _ = self.transform.SetOutputType(self.output_id, &offered, 0);
                        }
                        self.read_sequence_header();
                        return Ok(None);
                    }
                    if e.code() == MF_E_TRANSFORM_NEED_MORE_INPUT {
                        return Ok(None);
                    }
                    return Err(format!("ProcessOutput: {e}"));
                }
                let Some(sample) = sample else { return Ok(None) };
                let key = sample.GetUINT32(&MFSampleExtension_CleanPoint).unwrap_or(0) != 0;
                let time = sample.GetSampleTime().unwrap_or(0) / HNS_PER_US;
                let buffer = sample.ConvertToContiguousBuffer().map_err(|e| e.to_string())?;
                let mut data: *mut u8 = std::ptr::null_mut();
                let mut length = 0u32;
                buffer.Lock(&mut data, None, Some(&mut length)).map_err(|e| e.to_string())?;
                let mut bytes = std::slice::from_raw_parts(data, length as usize).to_vec();
                let _ = buffer.Unlock();
                if key && !self.sequence_header.is_empty() && !nal_types(&bytes).contains(&7) {
                    // Some encoders send the parameter sets only once, in the format: a viewer who joins later
                    // needs them in front of every key frame.
                    let mut with = self.sequence_header.clone();
                    with.extend_from_slice(&bytes);
                    bytes = with;
                }
                Ok(Some((bytes, key, time)))
            }
        }
    }

    impl Drop for Encoder {
        fn drop(&mut self) {
            unsafe {
                let _ = self.transform.ProcessMessage(MFT_MESSAGE_NOTIFY_END_OF_STREAM, 0);
                let _ = self.transform.ProcessMessage(MFT_MESSAGE_COMMAND_FLUSH, 0);
                if let Ok(shutdown) = self.transform.cast::<IMFShutdown>() {
                    let _ = shutdown.Shutdown();
                }
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    pub fn run(
        source: NativeVideoSource,
        width: u32,
        height: u32,
        fps: u32,
        bitrate: u64,
        slot: SlotApi,
        stop: Arc<AtomicBool>,
        encoded: Arc<AtomicU32>,
        ready: std::sync::mpsc::Sender<Result<String, String>>,
        on_failure: OnFailure,
    ) {
        unsafe {
            let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
            if let Err(e) = MFStartup(MF_VERSION, MFSTARTUP_FULL) {
                let _ = ready.send(Err(format!("MFStartup: {e}")));
                CoUninitialize();
                return;
            }
        }
        let result = (|| -> Result<(), String> {
            let (mut encoder, name) = match Encoder::open(width, height, fps, bitrate) {
                Ok(v) => v,
                Err(e) => {
                    let _ = ready.send(Err(e.clone()));
                    return Ok(()); // not a failure mid-share: the caller falls back right away
                }
            };
            let _ = ready.send(Ok(name));
            let mut current_bitrate = bitrate;
            let mut want_key = true;
            // `need`: pictures the encoder asked for; `in_flight`: pictures given whose output is still due.
            let (mut need, mut in_flight) = (0u32, 0u32);
            let resolution = VideoResolution { width, height };
            while !stop.load(Ordering::Relaxed) {
                // Hardware encoders are event driven. With nothing asked of us we sleep in GetEvent until the
                // encoder speaks; otherwise we only look whether it said something.
                let flags = if need == 0 { MEDIA_EVENT_GENERATOR_GET_EVENT_FLAGS(0) } else { MF_EVENT_FLAG_NO_WAIT };
                match unsafe { encoder.events.GetEvent(flags) } {
                    Ok(event) => {
                        let kind = unsafe { event.GetType() }.map_err(|e| e.to_string())?;
                        if kind == METransformNeedInput.0 as u32 {
                            need += 1;
                        } else if kind == METransformHaveOutput.0 as u32 {
                            in_flight = in_flight.saturating_sub(1);
                            if let Some((bytes, key, time)) = encoder.take_output()? {
                                let frame = EncodedVideoFrame {
                                    codec: EncodedVideoCodec::H264,
                                    payload: &bytes,
                                    timestamp_us: time,
                                    frame_type: if key { EncodedFrameType::Key } else { EncodedFrameType::Delta },
                                    resolution,
                                    frame_metadata: None,
                                };
                                source.capture_encoded_frame(&frame);
                                encoded.fetch_add(1, Ordering::Relaxed);
                            }
                        }
                        continue; // more events may be waiting
                    }
                    Err(e) if e.code() == MF_E_NO_EVENTS_AVAILABLE => {}
                    Err(e) => return Err(format!("GetEvent: {e}")),
                }
                // The encoder wants a picture. While one is still being encoded, look back for its output
                // within a millisecond; otherwise wait for the capture.
                let Some((picture, ts)) = slot.take(&stop, if in_flight > 0 { 1 } else { 200 }) else { continue };
                if source.take_keyframe_request() {
                    want_key = true;
                }
                if let Some(rate) = source.take_rate_control_request() {
                    let target = rate.target_bitrate_bps.clamp(300_000, bitrate.max(300_000));
                    if target.abs_diff(current_bitrate) * 10 > current_bitrate {
                        current_bitrate = target;
                        encoder.set_bitrate(target);
                    }
                }
                if want_key {
                    encoder.force_key_frame();
                    want_key = false;
                }
                encoder.feed(&picture, ts)?;
                need -= 1;
                in_flight += 1;
            }
            Ok(())
        })();
        if let Err(reason) = result {
            if !stop.load(Ordering::Relaxed) {
                log::warn!("Card encoder stopped: {reason}");
                on_failure(reason);
            }
        }
        unsafe {
            let _ = MFShutdown();
            CoUninitialize();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_parameter_sets_in_an_access_unit() {
        let au = [0, 0, 0, 1, 0x67, 1, 2, 0, 0, 1, 0x68, 3, 0, 0, 0, 1, 0x65, 9];
        assert_eq!(nal_types(&au), vec![7, 8, 5]);
    }
}
