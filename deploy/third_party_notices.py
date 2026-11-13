"""Writes THIRD_PARTY_NOTICES.md: every package of the client's environment with its license text.

Run it in the venv the installer is built from, before PyInstaller:
    python deploy/third_party_notices.py
The file ships inside the installer and is shown at <server>/licenses.
"""
from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Build tools and server-only packages that never reach the client installer.
SKIP = {"pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile",
        "pywin32-ctypes", "pytest", "pluggy", "iniconfig", "packaging", "alembic", "sqlalchemy", "mako",
        "fastapi", "starlette", "uvicorn", "argon2-cffi", "argon2-cffi-bindings", "pydantic-settings",
        "python-dotenv", "httptools", "uvloop", "watchfiles", "greenlet", "pygments", "colorama",
        "faster-whisper", "ruff"}

HEADER = """# Лицензии сторонних компонентов

VoiceChat построен на программах с открытым исходным кодом. Ниже перечислены компоненты, входящие в
установщик, и тексты их лицензий.

**Qt / PySide6** используются по лицензии GNU LGPL v3. Библиотеки Qt лежат в папке программы отдельными
файлами (_internal\\PySide6), их можно заменить своей сборкой той же версии. Исходный код Qt:
https://download.qt.io/official_releases/qt/ и https://code.qt.io/. Текст LGPL v3:
https://www.gnu.org/licenses/lgpl-3.0.html.

**LiveKit** (livekit, livekit-api, livekit-protocol) распространяется по Apache License 2.0 и включает
WebRTC (BSD-3-Clause) и кодек Opus (BSD-3-Clause).

**FFmpeg** (через PyAV; кодирование демонстрации экрана видеокартой) входит в виде отдельных библиотек
в папке _internal\\av.libs. Эта сборка FFmpeg включает x264 и x265 и поэтому распространяется по GNU GPL
версии 2 или новее; libavcodec и остальные библиотеки FFmpeg — LGPL v2.1+. Исходный код:
https://ffmpeg.org/download.html, https://code.videolan.org/videolan/x264, https://bitbucket.org/multicoreware/x265_git,
сборка: https://github.com/PyAV-Org/pyav-ffmpeg. Тексты лицензий: https://www.gnu.org/licenses/.

**RNNoise** (шумоподавление) — BSD-3-Clause, Xiph.Org Foundation и Mozilla.

**PortAudio** (через sounddevice) — лицензия PortAudio (MIT-подобная).

**Python** — Python Software Foundation License.

---
"""

LICENSE_FILES = ("LICENSE", "LICENSE.txt", "LICENSE.md", "LICENCE", "COPYING", "COPYING.txt", "NOTICE",
                 "LICENSE.rst", "AUTHORS")


def license_texts(dist: metadata.Distribution) -> list[str]:
    texts = []
    for file in dist.files or []:
        name = Path(str(file)).name
        if name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")) and "dist-info" in str(file) \
                or name in LICENSE_FILES and "licenses" in str(file).lower():
            try:
                text = Path(str(dist.locate_file(file))).read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if text and text not in texts:
                texts.append(text)
    return texts


def main() -> None:
    rows = []
    for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata["Name"] or "").lower()):
        name = dist.metadata["Name"] or ""
        if not name or name.lower() in SKIP:
            continue
        meta = dist.metadata
        license_name = meta.get("License-Expression") or ""
        if not license_name:
            classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or []
                           if c.startswith("License ::")]
            license_name = ", ".join(classifiers) or (meta.get("License") or "").splitlines()[0:1] and \
                (meta.get("License") or "").splitlines()[0][:120] or "см. текст ниже"
        home = meta.get("Home-page") or next((u.split(",", 1)[-1].strip() for u in meta.get_all("Project-URL") or []
                                               ), "")
        block = [f"## {name} {dist.version}", "", f"Лицензия: {license_name}" + (f" · {home}" if home else ""), ""]
        for text in license_texts(dist):
            block += ["```", text, "```", ""]
        rows.append("\n".join(block))
    out = HEADER + "\n" + "\n".join(rows)
    target = ROOT / "THIRD_PARTY_NOTICES.md"
    target.write_text(out, encoding="utf-8")
    print(f"{target}: {len(rows)} packages, {len(out) // 1024} KB", file=sys.stderr)


if __name__ == "__main__":
    main()
