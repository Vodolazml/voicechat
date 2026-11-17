# VoiceChat 0.6.2 (client only)

Evgen (60 Hz monitor, 2026-11-17): the main window drew ~140 fps (8,400 frames a minute) the whole
call while left open behind the game, on 0.5.8 and 0.6.1 alike: about half a core plus GPU work the game
lost. The owner: CS2 144 fps with VoiceChat, 210 without (overlay on).

- Cause of the redraws: the splash window's logo pulse ran forever, even with the splash hidden. Any
  running QML animation makes Qt redraw every shown window of the app at full speed. Spinners and the
  sign-in glow now also stop in hidden windows (an item there keeps visible: true). Test added that the
  interface has no running animation in a call (tests/test_ui_idle.py).
- If the window keeps drawing in the background anyway, the Usage log line is followed by the list of
  animations still running.
- Overlay over a fullscreen game (new setting "Поверх полноэкранной игры", default "Только когда
  говорят"): the overlay window exists above a game only while someone talks. Any window above a game
  takes away its direct path to the screen. Borderless games are detected too (foreground window
  covering its monitor).
- The home screen logo pulses three times instead of forever (~190 fps while the app was in front).
