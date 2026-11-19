//! Video frames for the app's window, through shared memory. Each picture source (a friend's screen, a
//! camera, our own preview) gets a named memory block with a few slots of packed I420 (Y, then U and V at
//! half size). We write a slot and send a short "vf" event naming it; the app copies the planes straight
//! into a video frame the graphics card draws. Nothing per frame goes through the pipe but that event.
//!
//! The app reads a slot right after the event, and we never write more than 30 frames a second per source,
//! so with three slots a slot is reused only after ~66 ms, long after the app has copied it.

use std::sync::atomic::{AtomicU64, Ordering};

use serde_json::json;

use crate::events::Events;

pub const SLOTS: usize = 3;

static NEXT_BLOCK: AtomicU64 = AtomicU64::new(1);

pub fn i420_size(width: u32, height: u32) -> usize {
    let (cw, ch) = ((width + 1) / 2, (height + 1) / 2);
    (width * height + 2 * cw * ch) as usize
}

pub struct FrameSink {
    key: i64,
    events: Events,
    block: Option<Block>,
    slot: usize,
}

impl FrameSink {
    pub fn new(key: i64, events: Events) -> Self {
        Self { key, events, block: None, slot: 0 }
    }

    /// Writes one picture given as three planes with their strides and tells the app.
    #[allow(clippy::too_many_arguments)]
    pub fn write(&mut self, width: u32, height: u32, y: &[u8], sy: u32, u: &[u8], su: u32, v: &[u8], sv: u32) {
        if width == 0 || height == 0 {
            return;
        }
        let size = i420_size(width, height);
        let reuse = matches!(&self.block, Some(b) if b.width == width && b.height == height);
        if !reuse {
            let name = format!("vcvideo-{}-{}-{}", std::process::id(), self.key, NEXT_BLOCK.fetch_add(1, Ordering::Relaxed));
            match Mapping::create(&name, size * SLOTS) {
                Ok(map) => self.block = Some(Block { name, width, height, map }),
                Err(e) => {
                    log::warn!("Video memory for {} not available: {e}", self.key);
                    return;
                }
            }
        }
        let Some(block) = self.block.as_mut() else { return };
        self.slot = (self.slot + 1) % SLOTS;
        let out = &mut block.map.bytes_mut()[self.slot * size..(self.slot + 1) * size];
        let (cw, ch) = (((width + 1) / 2) as usize, ((height + 1) / 2) as usize);
        let (w, h) = (width as usize, height as usize);
        let (out_y, rest) = out.split_at_mut(w * h);
        let (out_u, out_v) = rest.split_at_mut(cw * ch);
        copy_plane(out_y, y, sy as usize, w, h);
        copy_plane(out_u, u, su as usize, cw, ch);
        copy_plane(out_v, v, sv as usize, cw, ch);
        self.events.send(json!({"ev": "vf", "key": self.key, "name": block.name, "w": width, "h": height,
                                "slot": self.slot, "size": size}));
    }

    /// The source ended: the app drops its last picture.
    pub fn gone(&mut self) {
        if self.block.take().is_some() {
            self.events.send(json!({"ev": "vgone", "key": self.key}));
        }
    }
}

impl Drop for FrameSink {
    fn drop(&mut self) {
        self.gone();
    }
}

fn copy_plane(dst: &mut [u8], src: &[u8], stride: usize, width: usize, height: usize) {
    if stride == width && src.len() >= width * height {
        dst[..width * height].copy_from_slice(&src[..width * height]);
        return;
    }
    for row in 0..height {
        let from = row * stride;
        if from + width > src.len() {
            break;
        }
        dst[row * width..(row + 1) * width].copy_from_slice(&src[from..from + width]);
    }
}

struct Block {
    name: String,
    width: u32,
    height: u32,
    map: Mapping,
}


#[cfg(windows)]
struct Mapping {
    handle: windows_sys::Win32::Foundation::HANDLE,
    view: windows_sys::Win32::System::Memory::MEMORY_MAPPED_VIEW_ADDRESS,
    size: usize,
}

#[cfg(windows)]
unsafe impl Send for Mapping {}

#[cfg(windows)]
impl Mapping {
    /// A named block in the page file, "Local\\<name>": the app opens it with mmap(-1, size, tagname).
    fn create(name: &str, size: usize) -> Result<Self, String> {
        use windows_sys::Win32::Foundation::{CloseHandle, INVALID_HANDLE_VALUE};
        use windows_sys::Win32::System::Memory::{CreateFileMappingW, MapViewOfFile, FILE_MAP_ALL_ACCESS, PAGE_READWRITE};
        let wide: Vec<u16> = format!("Local\\{name}").encode_utf16().chain(std::iter::once(0)).collect();
        unsafe {
            let handle = CreateFileMappingW(
                INVALID_HANDLE_VALUE,
                std::ptr::null(),
                PAGE_READWRITE,
                (size as u64 >> 32) as u32,
                size as u32,
                wide.as_ptr(),
            );
            if handle.is_null() {
                return Err(format!("CreateFileMapping failed ({})", std::io::Error::last_os_error()));
            }
            let view = MapViewOfFile(handle, FILE_MAP_ALL_ACCESS, 0, 0, size);
            if view.Value.is_null() {
                CloseHandle(handle);
                return Err(format!("MapViewOfFile failed ({})", std::io::Error::last_os_error()));
            }
            Ok(Self { handle, view, size })
        }
    }

    fn bytes_mut(&mut self) -> &mut [u8] {
        unsafe { std::slice::from_raw_parts_mut(self.view.Value as *mut u8, self.size) }
    }
}

#[cfg(windows)]
impl Drop for Mapping {
    fn drop(&mut self) {
        use windows_sys::Win32::Foundation::CloseHandle;
        use windows_sys::Win32::System::Memory::UnmapViewOfFile;
        unsafe {
            UnmapViewOfFile(self.view);
            CloseHandle(self.handle);
        }
    }
}

#[cfg(not(windows))]
struct Mapping {
    path: std::path::PathBuf,
    map: memmap2::MmapMut,
}

#[cfg(not(windows))]
impl Mapping {
    /// A file in /dev/shm (tests and development on Linux): the app maps the same file.
    fn create(name: &str, size: usize) -> Result<Self, String> {
        let dir = std::path::Path::new("/dev/shm");
        let path = if dir.is_dir() { dir.join(name) } else { std::env::temp_dir().join(name) };
        let file = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(true)
            .open(&path)
            .map_err(|e| e.to_string())?;
        file.set_len(size as u64).map_err(|e| e.to_string())?;
        let map = unsafe { memmap2::MmapMut::map_mut(&file) }.map_err(|e| e.to_string())?;
        Ok(Self { path, map })
    }

    fn bytes_mut(&mut self) -> &mut [u8] {
        &mut self.map
    }
}

#[cfg(not(windows))]
impl Drop for Mapping {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn planes_are_packed_without_stride_padding() {
        let mut dst = vec![0u8; 6];
        let src = [1, 2, 3, 9, 4, 5, 6, 9];
        copy_plane(&mut dst, &src, 4, 3, 2);
        assert_eq!(dst, [1, 2, 3, 4, 5, 6]);
        assert_eq!(i420_size(3, 3), 9 + 2 * 4);
    }
}
