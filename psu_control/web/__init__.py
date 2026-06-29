"""Web UI for controlling an ITECH IT-N6332B power supply.

Run it with::

    python -m psu_control.web --port 8080            # then connect from the UI
    python -m psu_control.web --demo                 # built-in simulator, no hardware

See :mod:`psu_control.web.server` for the implementation.
"""

from .server import Controller, create_server, main

__all__ = ["Controller", "create_server", "main"]
