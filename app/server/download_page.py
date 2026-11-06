"""The public front page: one button that downloads the Windows app.

The app installs once; after that it updates itself at every start, so this page always hands out the
latest signed installer (the same file the in-app updater uses).
"""
from __future__ import annotations

import html

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .config import get_settings
from ..version import APP_VERSION

router = APIRouter(include_in_schema=False)

PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VoiceChat</title>
<style>
  :root { color-scheme: dark; --accent: #7c5cff; --pink: #ff3d9a; --text: #eef0f6; --muted: #9aa0b4; }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px 16px;
         font-family: "Segoe UI Variable Text", "Segoe UI", Inter, system-ui, sans-serif; color: var(--text);
         background: radial-gradient(60vw 60vw at 10% 10%, #2a1f5c55, transparent 60%),
                     radial-gradient(50vw 50vw at 90% 15%, #0f4a5544, transparent 60%),
                     radial-gradient(60vw 60vw at 60% 100%, #5c1f4a44, transparent 60%), #0d0e13; }
  .card { width: 100%; max-width: 460px; padding: 36px 32px; border-radius: 22px; text-align: center;
          background: rgba(20, 21, 30, .86); border: 1px solid rgba(255, 255, 255, .07); }
  .logo { width: 80px; height: 80px; margin: 0 auto 18px; filter: drop-shadow(0 12px 32px rgba(255, 90, 114, .35)); }
  h1 { margin: 0 0 6px; font-size: 28px; }
  p { margin: 0; color: var(--muted); line-height: 1.5; }
  .button { display: inline-flex; align-items: center; gap: 10px; margin: 26px 0 10px; padding: 14px 26px;
            border-radius: 14px; color: #fff; font-weight: 600; font-size: 16px; text-decoration: none;
            background: linear-gradient(90deg, var(--accent), #a14dff); }
  .button:hover { filter: brightness(1.1); }
  .button.off { background: #2a2c38; color: var(--muted); pointer-events: none; }
  .small { font-size: 13px; }
  ol { text-align: left; color: var(--muted); margin: 22px 0 0; padding-left: 20px; line-height: 1.7; font-size: 14px; }
  ol b { color: var(--text); font-weight: 600; }
</style>
</head>
<body>
<main class="card">
  <div class="logo"><svg width="80" height="80" viewBox="0 0 256 256" aria-hidden="true">
    <defs><linearGradient id="g" x1="0" y1="0" x2="256" y2="0" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#ff7849"/><stop offset="1" stop-color="#ff3d9a"/>
    </linearGradient></defs>
    <rect width="256" height="256" rx="85" fill="url(#g)"/>
    <g fill="#fff"><rect x="76" y="116.4" width="11.6" height="23.1" rx="5.8"/><rect x="99.1" y="93.3" width="11.6" height="69.3" rx="5.8"/>
    <rect x="122.2" y="70.2" width="11.6" height="115.6" rx="5.8"/><rect x="145.3" y="93.3" width="11.6" height="69.3" rx="5.8"/>
    <rect x="168.4" y="116.4" width="11.6" height="23.1" rx="5.8"/></g></svg></div>
  <h1>VoiceChat</h1>
  <p>Голос, демонстрация экрана и чат для своих. Голос и экран зашифрованы от устройства до устройства.</p>
  {button}
  <p class="small">{note}</p>
  <ol>
    <li><b>Скачайте и запустите</b> установщик. Права администратора не нужны.</li>
    <li><b>Зарегистрируйтесь</b> в программе и введите код приглашения от друга.</li>
    <li><b>Всё.</b> Обновления программа ставит сама при запуске.</li>
  </ol>
</main>
</body>
</html>
"""


def _installer_url() -> str:
    return get_settings().client_download_url


@router.get("/", response_class=HTMLResponse)
def front_page() -> Response:
    settings = get_settings()
    if _installer_url():
        version = html.escape(settings.client_latest_version)
        button = ('<a class="button" href="/download">'
                  '<svg width="20" height="20" viewBox="0 0 24 24" fill="#fff" aria-hidden="true">'
                  '<path d="M5 20h14v-2H5v2zm7-18-5.5 5.5 1.4 1.4L11 5.8V16h2V5.8l3.1 3.1 1.4-1.4L12 2z" '
                  'transform="rotate(180 12 11)"/></svg>Скачать для Windows</a>')
        note = f"Версия {version} · Windows 10 и 11"
    else:
        button = '<span class="button off">Установщик скоро появится</span>'
        note = f"Сервер работает, версия {html.escape(APP_VERSION)}"
    page = PAGE.replace("{button}", button).replace("{note}", note)
    return HTMLResponse(page, headers={"Content-Security-Policy":
                                       "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
                                       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"})


@router.get("/download")
def download_latest() -> Response:
    """A stable link to the newest installer, for the page and for sharing."""
    url = _installer_url()
    if not url:
        return HTMLResponse("Установщик ещё не опубликован", status_code=404)
    return RedirectResponse(url, status_code=302)
