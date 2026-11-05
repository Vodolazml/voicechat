import sys


if __name__ == "__main__":
    if "--classic" in sys.argv:
        # The previous widget-based client, kept for one release as a fallback.
        from app.client.main import main
    else:
        from app.client.ui.app import main
    raise SystemExit(main())
