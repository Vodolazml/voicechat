# VoiceChat 0.6.3 (server + client)

## Telegram bot (server)
- One bot per server (VOICECHAT_TELEGRAM_BOT_TOKEN), long polling from the API, no webhook.
- Everyone can link their account from the app (Settings -> Аккаунт и устройства -> Подключить Telegram):
  the app opens t.me/<bot>?start=<one-time code, 15 min>. The bot then writes when someone joins a voice
  channel of the person's spaces while the person is not in it (once per person and channel in 10 min).
  Off in the app or with /notify; /stop unlinks.
- Admin information only for the server owner's linked chat (users.is_owner, one account) and the
  optional VOICECHAT_TELEGRAM_ADMIN_CHAT_ID; a person given the admin role does not get it.

## Downloads approved by the owner
- With an admin chat known, every download from the web page waits: the owner gets the IP, device
  (system and browser) and language with Allow / Deny, and the visitor's page starts the download once
  allowed (refreshes every 3 s, no scripts).
- The visitor's page shows only a neutral status (Ожидает подтверждения / Разрешено / Отказано): nothing
  about the owner, the bot or who decides.
- Installers are served by Caddy only with a signed link bound to the requester's IP (forward_auth to
  /downloads-auth). An approved web link works 1 h.
- Updates only for signed-in people, without approval: /client/update gives the link only with a valid
  Bearer token (VOICECHAT_DOWNLOAD_LINK_HOURS, default 6); the owner is told who updated (once per IP and
  version in 6 h). 0.6.3 resumes the remembered session before its startup update check; 0.6.0-0.6.2
  check again after sign-in and show the "Обновить" banner.
- Counter in download-stats.json next to the chat files (/stats in the bot).

## Server login alert
- deploy/ssh-login-alert.sh: pam_exec hook, every successful SSH login goes to the owner's chat.

## Deploy
- Caddyfile changed (downloads behind forward_auth), migration 0008 (telegram_links).
- .env: VOICECHAT_TELEGRAM_BOT_TOKEN, VOICECHAT_TELEGRAM_ADMIN_CHAT_ID (values in /etc/voicechat/telegram.env).
