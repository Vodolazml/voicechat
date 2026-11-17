//! Shortcut keys during a call, read here instead of in the app so that Python is not woken 66 times a
//! second. Push-to-talk acts on the engine directly (no round trip); the other shortcuts become a "key"
//! event for the app. Only while the app asks for it (in a call with shortcuts set).

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use parking_lot::Mutex;
use serde_json::json;

use crate::engine::Engine;
use crate::events::Events;

#[derive(Default)]
struct State {
    combos: HashMap<String, Vec<i32>>,
    down: HashMap<String, bool>,
}

pub struct Keys {
    state: Arc<Mutex<State>>,
    thread: std::thread::Thread,
}

impl Keys {
    pub fn start(engine: Arc<Engine>, events: Events) -> Self {
        let state = Arc::new(Mutex::new(State::default()));
        let shared = state.clone();
        let handle = std::thread::Builder::new()
            .name("keys".into())
            .spawn(move || loop {
                let idle = shared.lock().combos.is_empty();
                if idle {
                    std::thread::park_timeout(Duration::from_secs(5));
                    continue;
                }
                let changes = {
                    let mut st = shared.lock();
                    let mut changes = Vec::new();
                    let names: Vec<String> = st.combos.keys().cloned().collect();
                    for name in names {
                        let down = st.combos[&name].iter().all(|&vk| key_down(vk));
                        if st.down.get(&name).copied().unwrap_or(false) != down {
                            st.down.insert(name.clone(), down);
                            changes.push((name, down));
                        }
                    }
                    changes
                };
                for (name, down) in changes {
                    if name == "ptt" {
                        engine.set_ptt(down);
                    }
                    events.send(json!({"ev": "key", "name": name, "down": down}));
                }
                std::thread::sleep(Duration::from_millis(15));
            })
            .expect("keys thread");
        Self { state, thread: handle.thread().clone() }
    }

    /// New shortcuts (name -> virtual key codes); empty stops reading the keyboard.
    pub fn set(&self, combos: HashMap<String, Vec<i32>>) {
        let mut st = self.state.lock();
        st.down = combos.keys().map(|name| (name.clone(), false)).collect();
        st.combos = combos;
        drop(st);
        self.thread.unpark();
    }
}

#[cfg(windows)]
fn key_down(vk: i32) -> bool {
    unsafe { windows_sys::Win32::UI::Input::KeyboardAndMouse::GetAsyncKeyState(vk) as u16 & 0x8000 != 0 }
}

#[cfg(not(windows))]
fn key_down(_vk: i32) -> bool {
    false
}
