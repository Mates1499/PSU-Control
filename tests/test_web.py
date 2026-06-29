"""Tests for the triple-channel web UI backend, run against the simulator.

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


def test_connect_demo_reports_three_channels():
    with _Server() as s:
        status, st = s.call("POST", "/api/connect", {"demo": True})
        assert status == 200 and st["connected"] is True
        assert len(st["channels"]) == 3
        names = {c["name"] for c in st["channels"]}
        assert names == {"CH1", "CH2", "CH3"}
        ch3 = next(c for c in st["channels"] if c["name"] == "CH3")
        assert ch3["max_voltage"] == 5.0 and ch3["max_current"] == 3.0


def test_channel_setpoint_and_measure():
    with _Server() as s:
        s.call("POST", "/api/connect", {"demo": True})
        status, st = s.call("POST", "/api/channel/1/setpoint", {"voltage": 30, "current": 6})
        assert status == 200
        s.call("POST", "/api/channel/1/output", {"on": True})
        _, m = s.call("GET", "/api/measure")
        ch1 = next(c for c in m["channels"] if c["number"] == 1)
        # 30 V into the simulator's 30 ohm load -> ~1 A.
        assert abs(ch1["measurement"]["current"] - 1.0) < 0.3


def test_channel_range_rejected():
    with _Server() as s:
        s.call("POST", "/api/connect", {"demo": True})
        # CH3 maxes at 5 V.
        status, body = s.call("POST", "/api/channel/3/setpoint", {"voltage": 30})
        assert status == 400
        assert "range" in body["error"].lower()


def test_all_output_toggle():
    with _Server() as s:
        s.call("POST", "/api/connect", {"demo": True})
        _, m = s.call("POST", "/api/all_output", {"on": True})
        assert all(c["output"] for c in m["channels"])
        _, m = s.call("POST", "/api/all_output", {"on": False})
        assert not any(c["output"] for c in m["channels"])


def test_error_when_not_connected():
    with _Server() as s:
        status, body = s.call("POST", "/api/channel/1/output", {"on": True})
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
