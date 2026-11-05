import sys


if __name__ == "__main__":
    import multiprocessing

    # Screen capture runs in a child process; in the installed (frozen) app that child starts this same exe,
    # and this call turns it into the capture worker instead of a second copy of the app.
    multiprocessing.freeze_support()
    if "--classic" in sys.argv:
        # The previous widget-based client, kept for one release as a fallback.
        from app.client.main import main
    else:
        from app.client.ui.app import main
    raise SystemExit(main())
