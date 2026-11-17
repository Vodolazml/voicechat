# VoiceChat 0.6.1 (client only)

First live check of 0.6.0 (PC1, CS2, 2026-11-17): VoiceChat + engine ≈ 0.8% of all CPU, 0.1% GPU, no
DWM change. What remained: ~950 MB private memory, 274 threads, and two LiveKit participants per user.

- The video connection (Python LiveKit room for screen share and camera) is opened only while someone
  else in the channel shares a screen or a camera, or while we share; closed 20 s after the last one
  ends. The voice engine tells the app who publishes video (new "video" event).
- Users being watched are remembered and given to a video connection opened later.
- Engine log: WebRTC's own warnings (STUN/TURN sends failing on virtual adapters such as WireGuard and
  Hyper-V: 80 lines in 15 s) no longer reach the log; only its errors, each at most once a minute.
