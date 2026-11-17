# VoiceChat 0.5.9 (client only)

Minimised VoiceChat used more than an open one (2026-11-17: CPU 2%/GPU 0% open, 6%/6% minimised).
The game overlay drew ~12,000 frames a minute (the monitor rate) while friends talked: every
"speaking" flip between words restarted the halo fade, and a transparent topmost window redrawn
that often makes Windows recompose the screen too.

- A speaker lights up at once and goes dark only after 0.4 s of quiet (friends and yourself), so pauses
  between words no longer flip the highlight.
- The overlay's speaking halo appears and disappears instantly (no fade animations over games).
- In the background the mic level is not tracked for the UI (only the overlay is visible, it has no meter).
- Usage log line now also counts speaking changes, next to window and overlay frames.
