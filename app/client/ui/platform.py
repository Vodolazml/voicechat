"""Small Windows integrations, all no-ops on other systems.

- Keys are read with GetAsyncKeyState polling instead of RegisterHotKey: a game keeps receiving the key
  (RegisterHotKey would swallow it), and push-to-talk works while the game has focus.
- A game in exclusive fullscreen is detected so nothing ever tries to come to the front.
- During a call the process opts out of Windows power throttling (EcoQoS), so minimising the window
  does not slow down audio threads.
- The overlay window is made click-through and never activatable at the Win32 level.
"""
from __future__ import annotations

import ctypes
import logging
import sys

logger = logging.getLogger("voicechat.platform")
IS_WINDOWS = sys.platform == "win32"

# Virtual-key codes for names shown in settings.
_NAMED_KEYS = {
    "Mouse4": 0x05, "Mouse5": 0x06, "Backspace": 0x08, "Tab": 0x09, "Enter": 0x0D, "Shift": 0x10,
    "Ctrl": 0x11, "Alt": 0x12, "Pause": 0x13, "CapsLock": 0x14, "Esc": 0x1B, "Space": 0x20,
    "PageUp": 0x21, "PageDown": 0x22, "End": 0x23, "Home": 0x24, "Left": 0x25, "Up": 0x26, "Right": 0x27,
    "Down": 0x28, "Insert": 0x2D, "Delete": 0x2E, "Num0": 0x60, "Num1": 0x61, "Num2": 0x62, "Num3": 0x63,
    "Num4": 0x64, "Num5": 0x65, "Num6": 0x66, "Num7": 0x67, "Num8": 0x68, "Num9": 0x69, "Num*": 0x6A,
    "Num+": 0x6B, "Num-": 0x6D, "Num.": 0x6E, "Num/": 0x6F, "ScrollLock": 0x91, "`": 0xC0, "~": 0xC0,
    "LShift": 0xA0, "RShift": 0xA1, "LCtrl": 0xA2, "RCtrl": 0xA3, "LAlt": 0xA4, "RAlt": 0xA5,
}
for _i in range(1, 25):
    _NAMED_KEYS[f"F{_i}"] = 0x6F + _i
_MODIFIERS = {"Ctrl": 0x11, "Shift": 0x10, "Alt": 0x12}


def parse_combo(text: str) -> tuple[int, ...] | None:
    """'Ctrl+Shift+M' -> virtual keys. Returns None for an empty or unknown combo."""
    parts = [part.strip() for part in (text or "").split("+") if part.strip()]
    if not parts:
        return None
    keys: list[int] = []
    for part in parts:
        if part in _NAMED_KEYS:
            keys.append(_NAMED_KEYS[part])
        elif len(part) == 1 and part.upper().isalnum():
            keys.append(ord(part.upper()))
        else:
            return None
    return tuple(keys)


def qt_key_name(key: int, text: str) -> str:
    """Name for a key pressed in the settings 'press a key' field (Qt key code)."""
    from PySide6.QtCore import Qt

    special = {
        Qt.Key_Space: "Space", Qt.Key_Tab: "Tab", Qt.Key_CapsLock: "CapsLock", Qt.Key_Escape: "Esc",
        Qt.Key_Insert: "Insert", Qt.Key_Delete: "Delete", Qt.Key_Home: "Home", Qt.Key_End: "End",
        Qt.Key_PageUp: "PageUp", Qt.Key_PageDown: "PageDown", Qt.Key_Left: "Left", Qt.Key_Right: "Right",
        Qt.Key_Up: "Up", Qt.Key_Down: "Down", Qt.Key_Pause: "Pause", Qt.Key_ScrollLock: "ScrollLock",
        Qt.Key_QuoteLeft: "`", Qt.Key_AsciiTilde: "~", Qt.Key_Backspace: "Backspace", Qt.Key_Return: "Enter",
    }
    if key in special:
        return special[key]
    if Qt.Key_F1 <= key <= Qt.Key_F24:
        return f"F{key - Qt.Key_F1 + 1}"
    if Qt.Key_A <= key <= Qt.Key_Z or Qt.Key_0 <= key <= Qt.Key_9:
        return chr(key)
    return ""


class KeyPoller:
    """Edge-detects key combos. `poll()` returns names of combos that were just pressed or released."""

    def __init__(self) -> None:
        self._user32 = ctypes.windll.user32 if IS_WINDOWS else None
        self._combos: dict[str, tuple[int, ...]] = {}
        self._down: dict[str, bool] = {}

    @property
    def available(self) -> bool:
        return self._user32 is not None

    def set_combos(self, combos: dict[str, str]) -> None:
        self._combos = {name: keys for name, text in combos.items() if (keys := parse_combo(text))}
        self._down = {name: False for name in self._combos}

    def is_down(self, name: str) -> bool:
        return self._down.get(name, False)

    def poll(self) -> list[tuple[str, bool]]:
        if self._user32 is None:
            return []
        changes = []
        state = self._user32.GetAsyncKeyState
        for name, keys in self._combos.items():
            down = all(state(key) & 0x8000 for key in keys)
            if down != self._down[name]:
                self._down[name] = down
                changes.append((name, down))
        return changes


def foreground_is_fullscreen() -> bool:
    """True while a fullscreen game or presentation is running (don't pop anything up then)."""
    if not IS_WINDOWS:
        return False
    try:
        state = ctypes.c_int(0)
        ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
        # 2 busy (fullscreen app), 3 D3D exclusive fullscreen, 4 presentation mode
        return state.value in (2, 3, 4)
    except Exception:
        return False


class _PowerThrottling(ctypes.Structure):
    _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]


def set_call_active(active: bool) -> None:
    """Keep full CPU speed and prevent sleep while in a call; restore normal behaviour afterwards."""
    if not IS_WINDOWS:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        info = _PowerThrottling(1, 0x1, 0x0 if active else 0x1)  # EXECUTION_SPEED: off during calls
        kernel32.SetProcessInformation(kernel32.GetCurrentProcess(), 4, ctypes.byref(info), ctypes.sizeof(info))
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if active else 0))
        ctypes.windll.winmm.timeBeginPeriod(1) if active else ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        logger.debug("Power settings not applied", exc_info=True)


def make_click_through(win_id: int) -> None:
    """Overlay: mouse clicks pass to the game, the window never takes focus or shows in Alt+Tab."""
    if not IS_WINDOWS or not win_id:
        return
    try:
        user32 = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x20, 0x80
        WS_EX_NOACTIVATE, WS_EX_TOPMOST = 0x08000000, 0x8
        get = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        put = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        style = get(win_id, GWL_EXSTYLE)
        # No WS_EX_LAYERED here: Qt already made the window input-transparent, and forcing the layered
        # style afterwards switched off its per-pixel transparency (the background opacity stopped working).
        put(win_id, GWL_EXSTYLE, style | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST)
    except Exception:
        logger.debug("Overlay style not applied", exc_info=True)


def _colorref(hex_color: str) -> int:
    value = hex_color.lstrip("#")
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return r | (g << 8) | (b << 16)


def style_title_bar(win_id: int, caption: str, text: str, border: str) -> None:
    """Dark window caption in the app's own colours instead of the light system one.

    Windows 10 (20H1+) gets the dark caption; Windows 11 also takes the exact caption, text and border
    colours. The system buttons, snapping and resizing stay native."""
    if not IS_WINDOWS or not win_id:
        return
    try:
        dwm = ctypes.windll.dwmapi
        hwnd = ctypes.c_void_p(win_id)

        def put(attribute: int, value: int) -> None:
            data = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data))

        for dark_mode in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (19 on builds before 20H1)
            put(dark_mode, 1)
        put(35, _colorref(caption))   # DWMWA_CAPTION_COLOR
        put(36, _colorref(text))      # DWMWA_TEXT_COLOR
        put(34, _colorref(border))    # DWMWA_BORDER_COLOR
    except Exception:
        logger.debug("Title bar colours not applied", exc_info=True)


def set_autostart(enabled: bool, command: str) -> None:
    if not IS_WINDOWS:
        return
    import winreg

    path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, "PrivateVoiceChat", 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, "PrivateVoiceChat")
            except FileNotFoundError:
                pass
