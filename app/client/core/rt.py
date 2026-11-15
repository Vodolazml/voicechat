"""Priority for the few threads that must never wait: the ones that carry the voice.

Everything else in the app may wait for a game. While the app is in the background the whole process
runs below normal priority (ui/platform.set_background), and Windows' power saving applies to it as
to any program. Only the voice threads are registered with Windows' audio scheduler (MMCSS, as
players and Discord do) and exempt from power throttling, so a busy game does not make speech stutter.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading

logger = logging.getLogger(__name__)

_done = threading.local()


class _PowerThrottling(ctypes.Structure):
    _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]


def boost_voice_thread(task: str = "Pro Audio") -> None:
    """Call from a voice thread; does the work once per thread and is cheap afterwards."""
    if getattr(_done, "yes", False):
        return
    _done.yes = True
    if sys.platform != "win32":
        return
    try:
        index = ctypes.c_ulong(0)
        handle = ctypes.windll.avrt.AvSetMmThreadCharacteristicsW(task, ctypes.byref(index))
        info = _PowerThrottling(1, 0x1, 0x0)  # THREAD_POWER_THROTTLING_EXECUTION_SPEED: off
        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadInformation(kernel32.GetCurrentThread(), 3, ctypes.byref(info), ctypes.sizeof(info))
        # PortAudio's WASAPI streams register their own threads with MMCSS already; a second
        # registration then fails, which is fine.
        error = 0 if handle else ctypes.windll.kernel32.GetLastError()
        logger.info("Voice thread %s: audio scheduling %s", threading.current_thread().name,
                    "on" if handle else f"not added (error {error}, PortAudio may have registered it)")
    except Exception:
        logger.debug("Voice thread priority not set", exc_info=True)
