# VoiceChat 0.5.8 (client only)

Fixes rasping ("хрипы") in everyone's voice.

- **Auto gain no longer clips.** WebRTC's automatic gain (inside the LiveKit audio module, no target
  setting) drove speech to exactly 0 dBFS nearly every minute for everyone, so loud syllables were cut off.
  It is now off; our own slow leveler brings speech to about -20 dBFS RMS (up to +12 dB for a quiet mic),
  and a soft limiter keeps every outgoing peak under -3 dBFS. The "Автоматическая громкость" setting
  switches our leveler.
- **Strong noise suppression runs continuously again.** 0.5.7 skipped RNNoise on near-silent frames to
  save processor time; as a recurrent network it then resumed on each word with stale state and rasped at
  word starts.
- **Overloaded mic guard.** A mic set too loud in Windows or the headset app (PC3, Logitech G733: peaks at
  -0.6..-3 dBFS) is turned down before processing (to about -6 dBFS peaks, at most -12 dB, recovering
  slowly), and after 30 such frames within a minute a hint asks to lower the mic level in Windows.
- **Report:** per-minute audio stats gain `sent_peak_db`, `overload_frames`, `clipped_frames`
  (processing output at full scale) and `headroom_db`; the Usage line counts overlay frames separately.
- **Interface:** the speaking avatar ring no longer pulses endlessly (it redrew the window at the monitor
  rate, ~233 fps on PC3, while friends talked); it fades in and out with speaking.
