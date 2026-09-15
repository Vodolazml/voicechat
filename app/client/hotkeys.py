"""Global (system-wide) hotkeys on Windows via RegisterHotKey, no extra dependency.

Qt's own QShortcut only fires while the app window has focus, which is not
useful for a voice app meant to run in the background (e.g. while gaming).
This uses the raw Win32 API instead, hooked into Qt's native event loop.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_user32 = ctypes.windll.user32 if sys.platform == "win32" else None

DEFAULT_BINDINGS: dict[str, str] = {
    "toggle_mute": "Ctrl+Shift+M",
    "toggle_deafen": "Ctrl+Shift+D",
    "toggle_screen_share": "Ctrl+Shift+S",
}

ACTION_LABELS: dict[str, str] = {
    "toggle_mute": "Включить/выключить микрофон",
    "toggle_deafen": "Включить/выключить входящий звук",
    "toggle_screen_share": "Включить/выключить демонстрацию экрана",
}


def _qt_key_to_vk(key: int) -> int | None:
    if Qt.Key.Key_A.value <= key <= Qt.Key.Key_Z.value:
        return key
    if Qt.Key.Key_0.value <= key <= Qt.Key.Key_9.value:
        return key
    if Qt.Key.Key_F1.value <= key <= Qt.Key.Key_F24.value:
        return 0x70 + (key - Qt.Key.Key_F1.value)
    return None


def sequence_to_win32(text: str) -> tuple[int, int] | None:
    """Converts a "Ctrl+Shift+M"-style string into (modifiers, virtual_key)."""
    if not text:
        return None
    sequence = QKeySequence(text)
    if sequence.isEmpty():
        return None
    combo = sequence[0]
    key = combo.key().value
    mods = combo.keyboardModifiers()
    vk = _qt_key_to_vk(key)
    if vk is None:
        return None
    modifiers = MOD_NOREPEAT
    if mods & Qt.KeyboardModifier.ControlModifier:
        modifiers |= MOD_CONTROL
    if mods & Qt.KeyboardModifier.ShiftModifier:
        modifiers |= MOD_SHIFT
    if mods & Qt.KeyboardModifier.AltModifier:
        modifiers |= MOD_ALT
    if mods & Qt.KeyboardModifier.MetaModifier:
        modifiers |= MOD_WIN
    if modifiers == MOD_NOREPEAT:
        return None
    return modifiers, vk


class HotkeyManager:
    """Registers global hotkeys against a native window handle and dispatches WM_HOTKEY."""

    def __init__(self, hwnd: int) -> None:
        self.hwnd = hwnd
        self._ids_by_action: dict[str, int] = {}
        self._actions_by_id: dict[int, str] = {}

    @staticmethod
    def available() -> bool:
        return _user32 is not None

    def apply(self, bindings: dict[str, str]) -> dict[str, str]:
        """Registers every binding, returns {action: error_message} for the ones that failed."""
        self.clear()
        errors: dict[str, str] = {}
        if not self.available():
            return {action: "Глобальные горячие клавиши поддерживаются только на Windows" for action in bindings if bindings.get(action)}
        for index, (action, text) in enumerate(bindings.items(), start=1):
            if not text:
                continue
            parsed = sequence_to_win32(text)
            if parsed is None:
                errors[action] = "Комбинация не поддерживается"
                continue
            modifiers, vk = parsed
            if not _user32.RegisterHotKey(self.hwnd, index, modifiers, vk):
                errors[action] = "Комбинация уже занята другой программой"
                continue
            self._ids_by_action[action] = index
            self._actions_by_id[index] = action
        return errors

    def clear(self) -> None:
        if self.available():
            for hotkey_id in self._actions_by_id:
                _user32.UnregisterHotKey(self.hwnd, hotkey_id)
        self._ids_by_action.clear()
        self._actions_by_id.clear()

    def action_for_message(self, message_id: int, w_param: int) -> str | None:
        if message_id != WM_HOTKEY:
            return None
        return self._actions_by_id.get(w_param)


def parse_native_message(message: int) -> wintypes.MSG:
    return wintypes.MSG.from_address(message)
