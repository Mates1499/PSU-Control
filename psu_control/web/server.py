"""Stdlib HTTP backend for the IT-N6332B web UI.

A single-process, dependency-free server (built on ``http.server``) that serves
the dashboard and exposes a small JSON REST API wrapping the :class:`ITN6332B`
driver. A global :class:`Controller` holds the one instrument connection (a lab
supply has a single owner), guarded by a lock so concurrent browser requests are
serialised onto the instrument.

JSON API:
    GET  /api/state            -> full snapshot (connection, setpoints, limits)
    GET  /api/measure          -> lightweight V/I/P + output + protection flags
    POST /api/connect          {host, port, visa, demo}
    POST /api/disconnect
    POST /api/output           {on: bool}
    POST /api/setpoint         {voltage?, current?, mode?}
    POST /api/protection       {ovp?, ocp?, opp?}
    POST /api/clear_protection
    POST /api/reset
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from .. import ITN6332B, OutputMode, PSUError
from ..scpi import DEFAULT_SCPI_PORT
from ..simulator import SimulatedInstrument

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}


class Controller:
    """Owns the single PSU connection and serialises access to it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._psu: Optional[ITN6332B] = None
        self._sim: Optional[SimulatedInstrument] = None
        self._idn: str = ""
        self._target: str = ""

    # -- connection -------------------------------------------------------

    def connect(
        self,
        *,
        host: str = "",
        port: int = DEFAULT_SCPI_PORT,
        visa: str = "",
        demo: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            self._teardown()
            if demo:
                self._sim = SimulatedInstrument().start()
                self._sim.noise = True  # lively readings for the chart
                self._psu = ITN6332B.open_tcp(self._sim.host, self._sim.port)
                self._target = "demo (built-in simulator)"
            elif visa:
                self._psu = ITN6332B.open_visa(visa)
                self._target = visa
            else:
                if not host:
                    raise PSUError("A host (or VISA resource, or demo mode) is required")
                self._psu = ITN6332B.open_tcp(host, port)
                self._target = f"{host}:{port}"
            self._idn = self._psu.idn()
        return self.state()

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            self._teardown()
        return {"connected": False}

    def _teardown(self) -> None:
        if self._psu is not None:
            try:
                self._psu.close()
            except Exception:
                pass
            self._psu = None
        if self._sim is not None:
            try:
                self._sim.close()
            except Exception:
                pass
            self._sim = None
        self._idn = ""
        self._target = ""

    def _require(self) -> ITN6332B:
        if self._psu is None:
            raise PSUError("Not connected to an instrument")
        return self._psu

    # -- reads ------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        """Full snapshot, including setpoints and limits (more round-trips)."""
        with self._lock:
            if self._psu is None:
                return {"connected": False}
            psu = self._psu
            pos, neg = psu.get_current_limits()
            meas = psu.measure_all()
            prot = psu.protection_status()
            return {
                "connected": True,
                "target": self._target,
                "idn": self._idn,
                "demo": self._sim is not None,
                "output": psu.output_enabled,
                "mode": psu.get_mode().name,
                "voltage_set": psu.get_voltage(),
                "current_set": psu.get_current_setpoint(),
                "ilimit_pos": pos,
                "ilimit_neg": neg,
                "measurement": {
                    "voltage": meas.voltage,
                    "current": meas.current,
                    "power": meas.power,
                },
                "protection": _prot_dict(prot),
            }

    def measure(self) -> dict[str, Any]:
        """Lightweight poll: measurement + output flag + protection flags."""
        with self._lock:
            if self._psu is None:
                return {"connected": False}
            psu = self._psu
            meas = psu.measure_all()
            return {
                "connected": True,
                "output": psu.output_enabled,
                "measurement": {
                    "voltage": meas.voltage,
                    "current": meas.current,
                    "power": meas.power,
                },
                "protection": _prot_dict(psu.protection_status()),
            }

    # -- writes -----------------------------------------------------------

    def set_output(self, on: bool) -> dict[str, Any]:
        with self._lock:
            self._require().set_output(bool(on))
        return self.measure()

    def set_setpoint(
        self,
        voltage: Optional[float] = None,
        current: Optional[float] = None,
        mode: Optional[str] = None,
    ) -> dict[str, Any]:
        with self._lock:
            psu = self._require()
            if mode is not None:
                psu.set_mode(OutputMode[mode.upper()])
            if voltage is not None:
                psu.set_voltage(float(voltage))
            if current is not None:
                psu.set_current_limit(float(current))
            psu.check_errors()
        return self.state()

    def set_protection(
        self,
        ovp: Optional[float] = None,
        ocp: Optional[float] = None,
        opp: Optional[float] = None,
    ) -> dict[str, Any]:
        with self._lock:
            psu = self._require()
            if ovp is not None:
                psu.set_ovp(float(ovp))
            if ocp is not None:
                psu.set_ocp(float(ocp))
            if opp is not None:
                psu.set_opp(float(opp))
            psu.check_errors()
        return self.state()

    def clear_protection(self) -> dict[str, Any]:
        with self._lock:
            self._require().clear_protection()
        return self.measure()

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._require().reset()
        return self.state()


def _prot_dict(prot) -> dict[str, Any]:
    return {
        "ovp": prot.ovp,
        "ocp": prot.ocp,
        "opp": prot.opp,
        "otp": prot.otp,
        "any": prot.any_tripped,
        "text": str(prot),
    }


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    controller: Controller  # injected on the server instance

    server_version = "ITN6332B-WebUI/1.0"

    def log_message(self, *args) -> None:  # quieter console
        pass

    # -- helpers ----------------------------------------------------------

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
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _serve_static(self, path: str) -> None:
        if path in ("/", ""):
            path = "/index.html"
        # Prevent directory traversal: only the basename is used.
        filename = os.path.basename(path)
        full = os.path.join(STATIC_DIR, filename)
        if not os.path.isfile(full):
            self.send_error(404, "Not found")
            return
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", _CONTENT_TYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self, method: str) -> None:
        ctrl = self.controller
        path = self.path.split("?", 1)[0]
        try:
            if method == "GET" and path == "/api/state":
                self._send_json(ctrl.state())
            elif method == "GET" and path == "/api/measure":
                self._send_json(ctrl.measure())
            elif method == "POST" and path == "/api/connect":
                d = self._read_json()
                self._send_json(
                    ctrl.connect(
                        host=str(d.get("host", "")).strip(),
                        port=int(d.get("port") or DEFAULT_SCPI_PORT),
                        visa=str(d.get("visa", "")).strip(),
                        demo=bool(d.get("demo", False)),
                    )
                )
            elif method == "POST" and path == "/api/disconnect":
                self._send_json(ctrl.disconnect())
            elif method == "POST" and path == "/api/output":
                self._send_json(ctrl.set_output(bool(self._read_json().get("on"))))
            elif method == "POST" and path == "/api/setpoint":
                d = self._read_json()
                self._send_json(
                    ctrl.set_setpoint(
                        voltage=_opt_float(d.get("voltage")),
                        current=_opt_float(d.get("current")),
                        mode=d.get("mode"),
                    )
                )
            elif method == "POST" and path == "/api/protection":
                d = self._read_json()
                self._send_json(
                    ctrl.set_protection(
                        ovp=_opt_float(d.get("ovp")),
                        ocp=_opt_float(d.get("ocp")),
                        opp=_opt_float(d.get("opp")),
                    )
                )
            elif method == "POST" and path == "/api/clear_protection":
                self._send_json(ctrl.clear_protection())
            elif method == "POST" and path == "/api/reset":
                self._send_json(ctrl.reset())
            elif method == "GET":
                self._serve_static(path)
            else:
                self.send_error(404, "Not found")
        except PSUError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500)

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
        description="Web UI for the ITECH IT-N6332B power supply.",
    )
    p.add_argument("--host", default="127.0.0.1", help="Address to bind (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8080, help="Port to listen on (default 8080)")
    p.add_argument(
        "--demo",
        action="store_true",
        help="Auto-connect to the built-in simulator on startup (no hardware).",
    )
    args = p.parse_args(argv)

    srv = create_server(args.host, args.port)
    ctrl: Controller = srv.RequestHandlerClass.controller  # type: ignore[attr-defined]
    if args.demo:
        try:
            ctrl.connect(demo=True)
            print("Demo mode: connected to built-in simulator.")
        except PSUError as exc:
            print(f"Demo connect failed: {exc}")

    url = f"http://{args.host}:{args.port}/"
    print(f"IT-N6332B web UI serving at {url}")
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
