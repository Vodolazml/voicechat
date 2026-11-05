# VoiceChat 0.3.1

Fixes
- Clicking in the settings no longer drops you into another voice channel (clicks went through the settings
  page to the channel list underneath).
- Voice keeps working while you share your screen: capture and scaling run in their own process, the audio
  device buffer is 40 ms instead of ~10 ms, and video tiles repaint at most 30 times a second.
- Sound no longer breaks when someone leaves: the new encryption key never reuses the old key slot, friends get
  3 s to fetch it, and WebRTC key ratcheting (a CPU spike on every undecryptable frame) is off.
- Switching channels keeps the headset open instead of closing and reopening the devices.
- Sign-up: a password needs 8+ characters with letters and a digit (no more upper-case/symbol puzzle); the form
  says so.
- Server: LiveKit 1.13 (the client SDK needed signalling fallbacks with 1.9).

New
- Spoken prompts (female voice): "Микрофон включён/выключен", "Звук включён/выключен"; can be turned off in
  Settings → Notifications.
- Every member of a space sees its invite code and can bring friends; only the creator can change the code.
- Channel order: right-click a channel → "Переместить выше/ниже" (space creator or server owner).
- Overlay: "Своё место" puts it anywhere on the screen with two sliders.
