"""Stdlib HTTP backend for the multi-channel PSU web UI.

A single-process, dependency-free server (built on ``http.server``) that serves
the dashboard and exposes a JSON REST API wrapping any :class:`BasePSUDriver`.
A global :class:`Controller` holds the one instrument connection, guarded by a
lock so concurrent browser requests are serialised onto it.

Supported models (passed as ``model`` in the ``/api/connect`` body):

* ``"itn6332b"`` (default) -- ITECH IT-N6332B (3-channel, bidirectional)
* ``"cpx200dp"``            -- Aim-TTi CPX200DP (2-channel, source-only)

JSON API
--------
::

    GET  /api/state                          -> connection + every channel
    GET  /api/measure                        -> per-channel V/I/P + output
    POST /api/connect                        {host, port, visa, model?}
    POST /api/disconnect
    POST /api/reset
    POST /api/all_output                     {on: bool}
    POST /api/channel/<n>/setpoint           {voltage?, current?, priority?}
    POST /api/channel/<n>/output             {on: bool}
    POST /api/channel/<n>/protection         {ovp?, ocp?, opp?}
    POST /api/channel/<n>/clear_protection
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from ..base import BasePSUDriver
from ..exceptions import PSUError
from ..it_n6332b import ITN6332B
from ..cpx200dp import CPX200DP
from ..scpi import DEFAULT_SCPI_PORT

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}

# Registry: model name -> (DriverClass, default_tcp_port)
_MODELS: dict[str, tuple] = {
    "itn6332b": (ITN6332B, DEFAULT_SCPI_PORT),
    "cpx200dp":  (CPX200DP,  CPX200DP.DEFAULT_TCP_PORT),
}


class Controller:
    """Owns the single PSU connection and serialises access to it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._psu: Optional[BasePSUDriver] = None
        self._idn: str = ""
        self._target: str = ""
        self._model: str = ""
        self._channels: list[int] = []
        self._ranges: dict[int, dict[str, Any]] = {}

    # -- connection -------------------------------------------------------

    def connect(
        self,
        *,
        host: str = "",
        port: int = 0,
        visa: str = "",
        model: str = "itn6332b",
    ) -> dict[str, Any]:
        model_key = model.lower().replace("-", "").replace("_", "")
        if model_key not in _MODELS:
            raise PSUError(
                f"Unknown model {model!r}. Choose from: {list(_MODELS)}"
            )
        DriverClass, default_port = _MODELS[model_key]

        with self._lock:
            self._teardown()
            self._model = model_key
            if visa:
                self._psu = DriverClass.open_visa(visa)
                self._target = visa
            else:
                if not host:
                    raise PSUError(
                        "Provide a host IP address or a VISA resource string."
                    )
                self._psu = DriverClass.open_tcp(host, port or default_port)
                self._target = f"{host}:{port or default_port}"
            self._idn = self._psu.idn()
            self._channels = self._psu.available_channels()
            self._ranges = {}
            for n in self._channels:
                try:
                    vlo, vhi = self._psu.channel(n).voltage_range()
                    ilo, ihi = self._psu.channel(n).current_range()
                    self._ranges[n] = {
                        "v_min": vlo, "v_max": vhi, "i_min": ilo, "i_max": ihi
                    }
                except Exception:
                    self._ranges[n] = {}
        return self.state()

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            self._teardown()
        return {"connected": False}

    def _teardown(self) -> None:
        try:
            if self._psu is not None:
                self._psu.close()
        except Exception:
            pass
        self._psu = None
        self._idn = ""
        self._target = ""
        self._model = ""
        self._channels = []
        self._ranges = {}

    def _require(self) -> BasePSUDriver:
        if self._psu is None:
            raise PSUError("Not connected to an instrument. Use /api/connect first.")
        return self._psu

    # -- reads ------------------------------------------------------------

    def _channel_state(self, n: int) -> dict[str, Any]:
        ch = self._psu.channel(n)  # type: ignore[union-attr]
        m = ch.measure()
        return {
            "number": n,
            "output": ch.output_enabled,
            "priority": ch.get_priority(),   # always a string: "VOLTAGE" / "CURRENT"
            "voltage_set": ch.get_voltage(),
            "current_set": ch.get_current(),
            "ranges": self._ranges.get(n, {}),
            "measurement": _meas(m),
            "mode": ch.regulation_mode(),
            "protection_tripped": ch.protection_tripped(),
        }

    def state(self) -> dict[str, Any]:
        with self._lock:
            if self._psu is None:
                return {"connected": False}
            return {
                "connected": True,
                "target": self._target,
                "idn": self._idn,
                "model": self._model,
                "channels": [self._channel_state(n) for n in self._channels],
            }

    def measure(self) -> dict[str, Any]:
        with self._lock:
            if self._psu is None:
                return {"connected": False}
            out = []
            for n in self._channels:
                ch = self._psu.channel(n)
                out.append({
                    "number": n,
                    "output": ch.output_enabled,
                    "measurement": _meas(ch.measure()),
                    "mode": ch.regulation_mode(),
                    "protection_tripped": ch.protection_tripped(),
                })
            return {"connected": True, "channels": out}

    # -- writes -----------------------------------------------------------

    def set_setpoint(
        self,
        number: int,
        voltage: Optional[float] = None,
        current: Optional[float] = None,
        priority: Optional[str] = None,
    ) -> dict[str, Any]:
        with self._lock:
            ch = self._require().channel(number)
            if priority is not None:
                ch.set_priority(priority.upper())
            if voltage is not None and current is not None:
                ch.apply(float(voltage), float(current))
            elif voltage is not None:
                ch.set_voltage(float(voltage))
            elif current is not None:
                ch.set_current(float(current))
            self._psu.check_errors()  # type: ignore[union-attr]
        return self.state()

    def set_output(self, number: int, on: bool) -> dict[str, Any]:
        with self._lock:
            self._require().channel(number).set_output(bool(on))
        return self.measure()

    def set_protection(
        self,
        number: int,
        ovp: Optional[float] = None,
        ocp: Optional[float] = None,
        opp: Optional[float] = None,
    ) -> dict[str, Any]:
        with self._lock:
            ch = self._require().channel(number)
            if ovp is not None:
                ch.set_ovp(float(ovp))
            if ocp is not None:
                ch.set_ocp(float(ocp))
            if opp is not None:
                ch.set_opp(float(opp))
            self._psu.check_errors()  # type: ignore[union-attr]
        return self.state()

    def clear_protection(self, number: int) -> dict[str, Any]:
        with self._lock:
            self._require().channel(number).clear_protection()
        return self.measure()

    def all_output(self, on: bool) -> dict[str, Any]:
        with self._lock:
            psu = self._require()
            psu.all_output_on() if on else psu.all_output_off()
        return self.measure()

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._require().reset()
        return self.state()


def _meas(m) -> dict[str, float]:
    return {"voltage": m.voltage, "current": m.current, "power": m.power}


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    controller: Controller
    server_version = "PSUControl-WebUI/4.0"

    def log_message(self, *args) -> None:
        pass

    def _send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _serve_static(self, path: str) -> None:
        if path in ("/", ""):
            path = "/index.html"
        full = os.path.join(STATIC_DIR, os.path.basename(path))
        if not os.path.isfile(full):
            self.send_error(404, "Not found")
            return
        with open(full, "rb") as fh:
            body = fh.read()
        ext = os.path.splitext(full)[1].lower()
        self.send_response(200)
        self.send_header(
            "Content-Type", _CONTENT_TYPES.get(ext, "application/octet-stream")
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self, method: str) -> None:
        ctrl = self.controller
        path = self.path.split("?", 1)[0]
        parts = [p for p in path.split("/") if p]
        try:
            if method == "GET" and path == "/api/state":
                self._send_json(ctrl.state())
            elif method == "GET" and path == "/api/measure":
                self._send_json(ctrl.measure())
            elif method == "POST" and path == "/api/connect":
                d = self._read_json()
                self._send_json(ctrl.connect(
                    host=str(d.get("host", "")).strip(),
                    port=int(d.get("port") or 0),
                    visa=str(d.get("visa", "")).strip(),
                    model=str(d.get("model", "itn6332b")),
                ))
            elif method == "POST" and path == "/api/disconnect":
                self._send_json(ctrl.disconnect())
            elif method == "POST" and path == "/api/reset":
                self._send_json(ctrl.reset())
            elif method == "POST" and path == "/api/all_output":
                self._send_json(ctrl.all_output(bool(self._read_json().get("on"))))
            elif method == "POST" and len(parts) == 4 and parts[:2] == ["api", "channel"]:
                n = int(parts[2])
                action = parts[3]
                d = self._read_json()
                if action == "setpoint":
                    self._send_json(ctrl.set_setpoint(
                        n,
                        _opt_float(d.get("voltage")),
                        _opt_float(d.get("current")),
                        d.get("priority"),
                    ))
                elif action == "output":
                    self._send_json(ctrl.set_output(n, bool(d.get("on"))))
                elif action == "protection":
                    self._send_json(ctrl.set_protection(
                        n,
                        _opt_float(d.get("ovp")),
                        _opt_float(d.get("ocp")),
                        _opt_float(d.get("opp")),
                    ))
                elif action == "clear_protection":
                    self._send_json(ctrl.clear_protection(n))
                else:
                    self.send_error(404, "Not found")
            elif method == "GET":
                self._serve_static(path)
            else:
                self.send_error(404, "Not found")
        except PSUError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
        except (ValueError, TypeError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:  # noqa: BLE001
            self._send_json(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500
            )

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")


def _opt_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    return float(value)


# --------------------------------------------------------------------------
# Server bootstrap
# --------------------------------------------------------------------------


def create_server(host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    """Build (but do not start) the threaded HTTP server."""
    handler = type("_BoundHandler", (_Handler,), {"controller": Controller()})
    return ThreadingHTTPServer((host, port), handler)


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="psu_control.web",
        description="Web UI for programmable DC power supplies.",
    )
    p.add_argument("--host", default="127.0.0.1", help="Address to bind (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8080, help="Port to listen on (default 8080)")
    p.add_argument(
        "--model",
        default="itn6332b",
        choices=list(_MODELS),
        help="PSU model (default: itn6332b)",
    )
    args = p.parse_args(argv)

    srv = create_server(args.host, args.port)

    print(f"PSU Control web UI serving at http://{args.host}:{args.port}/")
    print("Press Ctrl+C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        ctrl.disconnect()
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
