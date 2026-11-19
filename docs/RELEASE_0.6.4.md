# VoiceChat 0.6.4 (client only)

## Video in the native engine
- Screen shares, cameras and watching run in `vcvoice --video`, a second process of the voice engine that
  exists only while there is video. No LiveKit Python library, no screen-capture Python process.
- A crash in capture, the encoder or a camera driver ends only the video process: the voice goes on, the
  video process starts again (the person turns their share back on).
- Sharing: WebRTC's capturer (Windows Graphics Capture, Desktop Duplication fallback, mouse pointer
  included), libyuv scaling, H.264 on the graphics card through Media Foundation (NVIDIA, AMD, Intel),
  the processor (OpenH264) when there is no card encoder. Owner's PC, RTX 5070 Ti, 2560x1440 at 60 fps:
  58.7 of 58.7 fps encoded, about 1% CPU, video encoder 7-14%, 133 MB.
- Watching: decrypted and decoded in the engine, at most 30 pictures a second to the window through
  shared memory (`Local\vcvideo-*`), drawn by the graphics card as before.
- Camera: Media Foundation in the engine (best format up to 720p, 24 fps or more).
- Same connection ("u<id>" media token) and the same E2EE keys: 0.6.3 and older apps see new shares and
  cameras and the other way round (tested on a local LiveKit).
- `VOICECHAT_NATIVE_VIDEO=0` brings back the old Python video path.

## Not yet measured
- A/B in a real call against 0.6.3 (RAM, CPU, GPU while sharing and watching).
- A real webcam (the owner's PC has none).
