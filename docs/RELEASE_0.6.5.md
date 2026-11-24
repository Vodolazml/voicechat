# 0.6.5 (client only)

Screen share no longer disappears for viewers on a congested link.

On 2026-11-24 a 1440p share (sent as 1080p60 by the card encoder) kept encoding, but for minutes no frame left
the sharer: WebRTC's bandwidth estimate fell to 40–80 kbit/s and could not recover. A frame lost on the way out
makes WebRTC ask for a key frame on every following frame; the card answered each request with a big key frame
at no less than 300 kbit/s, which overflowed the link again. Only rejoining the channel helped.

- The card encoder stays within WebRTC's estimate: pictures are skipped before encoding (a token bucket on
  the bytes really produced), so WebRTC never has to drop an encoded frame and the H.264 chain stays whole.
  At least one picture a second still goes out, so a widening link is noticed. `skipped_fps` in the stats.
- Key frames asked for by the network: at most one a second. Encoder bitrate floor 100 kbit/s (was 300).
- If frames are made but none leave for 10 s while the estimate is collapsed, the video connection is opened
  again by itself (a fresh estimate), at most once a minute. The share and camera come back on their own.

Fallback stays: VOICECHAT_NATIVE_VIDEO=0 uses the old Python video path.
