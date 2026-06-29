"""Tests for the web UI backend, run against the built-in simulator (no hardware).

Run with::

    python -m pytest tests/test_web.py
    python tests/test_web.py
"""

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from psu_control.web.server import create_server  # noqa: E402


class _Server:
    def __enter__(self):
        self.srv = create_server("127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        self.ctrl = self.srv.RequestHandlerClass.controller
        self._t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self._t.start()
        time.sleep(0.1)
        return self

    def __exit__(self, *exc):
        self.ctrl.disconnect()
        self.srv.shutdown()
        self.srv.server_close()

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data,
            headers={"Content-Type": "application/json"}, method=method,
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


def test_static_index_served():
    with _Server() as s:
        with urllib.request.urlopen(f"http://127.0.0.1:{s.port}/") as r:
            body = r.read().decode()
        assert r.status == 200
        assert "IT-N6332B" in body


def test_connect_demo_reports_ranges():
    with _Server() as s:
        status, st = s.call("POST", "/api/connect", {"demo": True})
        assert status == 200 and st["connected"] is True
        assert "IT-N6332B" in st["idn"]
        assert st["priority"] in ("VOLTAGE", "CURRENT")
        r = st["ranges"]
        assert r["v_max"] > 0 and r["i_min"] < 0 < r["i_max"]  # bidirectional


def test_setpoint_priority_and_measure():
    with _Server() as s:
        s.call("POST", "/api/connect", {"demo": True})
        status, st = s.call("POST", "/api/setpoint",
                            {"voltage": 24, "current": 12, "priority": "VOLTAGE"})
        assert status == 200 and st["priority"] == "VOLTAGE"
        s.call("POST", "/api/output", {"on": True})
        _, m = s.call("GET", "/api/measure")
        # 24 V into the simulator's 12 ohm load -> ~2 A.
        assert abs(m["measurement"]["current"] - 2.0) < 0.3


def test_protection_roundtrip():
    with _Server() as s:
        s.call("POST", "/api/connect", {"demo": True})
        assert s.call("POST", "/api/protection", {"ovp": 13.5, "ocp": 2.5})[0] == 200
        assert s.call("POST", "/api/clear_protection", {})[0] == 200


def test_error_when_not_connected():
    with _Server() as s:
        status, body = s.call("POST", "/api/output", {"on": True})
        assert status == 400
        assert "connect" in body["error"].lower()


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t(); print(f"PASS {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1; print(f"FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
