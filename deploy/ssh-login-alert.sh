#!/bin/sh
# pam_exec hook (session open): every successful SSH login is sent to the owner's Telegram chat.
# Install: /usr/local/sbin/ssh-login-alert (root, 0755), and in /etc/pam.d/sshd:
#   session optional pam_exec.so quiet /usr/local/sbin/ssh-login-alert
# /etc/voicechat/telegram.env (root, 0600): TELEGRAM_BOT_TOKEN=... and TELEGRAM_ADMIN_CHAT_ID=...
[ "$PAM_TYPE" = "open_session" ] || exit 0
[ -r /etc/voicechat/telegram.env ] || exit 0
. /etc/voicechat/telegram.env
[ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_ADMIN_CHAT_ID" ] || exit 0
TEXT="🔐 Вход на сервер $(hostname): ${PAM_USER:-?} с IP ${PAM_RHOST:-?}, $(TZ=Europe/Moscow date '+%d.%m.%Y %H:%M') МСК"
# The token goes to curl on stdin, not on its command line (where ps would show it). In the background,
# so the login never waits for Telegram.
(
  printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$TELEGRAM_BOT_TOKEN" |
    timeout 15 curl -s -o /dev/null -K - --data-urlencode "chat_id=${TELEGRAM_ADMIN_CHAT_ID}" \
      --data-urlencode "text=${TEXT}"
) </dev/null >/dev/null 2>&1 &
exit 0
