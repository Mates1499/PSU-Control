"""Entry point for ``python -m psu_control.web``."""

from .server import main

if __name__ == "__main__":
    raise SystemExit(main())
