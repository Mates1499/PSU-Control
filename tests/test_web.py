"""Tests for the multi-channel web UI backend.

The server is started in-process.  Each test that needs an instrument spins up
a MockInstrument or CPX200DPSimulator on a random port and connects via the
regular host/port connect path (no demo mode).

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
from psu_control.simulator import SimulatedInstrument, CPX200DPSimulator  # noqa: E402


class _Server:
    def __enter__(self):
        self.srv = create_server("127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        self.ctrl = self.srv.RequestHandlerClass.controller
        self._t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self._t.start()
        self._sim = None
        time.sleep(0.1)
        return self

    def __exit__(self, *exc):
        self.ctrl.disconnect()
        if self._sim is not None:
            self._sim.close()
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

    def connect_itn6332b(self, channels=3):
        self._sim = SimulatedInstrument(channels=channels).start()
        return self.call("POST", "/api/connect", {
            "host": self._sim.host,
            "port": self._sim.port,
            "model": "itn6332b",
        })

    def connect_cpx200dp(self):
        self._sim = CPX200DPSimulator().start()
        return self.call("POST", "/api/connect", {
            "host": self._sim.host,
            "port": self._sim.port,
            "model": "cpx200dp",
        })


def test_static_index_served():
    with _Server() as s:
        with urllib.request.urlopen(f"http://127.0.0.1:{s.port}/") as r:
            body = r.read().decode()
        assert r.status == 200
        assert "IT-N6332B" in body


def test_connect_reports_channels():
    with _Server() as s:
        status, st = s.connect_itn6332b()
        assert status == 200 and st["connected"] is True
        assert [c["number"] for c in st["channels"]] == [1, 2, 3]
        ch1 = st["channels"][0]
        assert ch1["priority"] in ("VOLTAGE", "CURRENT")
        r = ch1["ranges"]
        assert r["i_min"] < 0 < r["i_max"]  # bidirectional


def test_per_channel_setpoint_and_measure():
    with _Server() as s:
        s.connect_itn6332b()
        s.call("POST", "/api/channel/1/setpoint", {"voltage": 24, "current": 12, "priority": "VOLTAGE"})
        s.call("POST", "/api/channel/2/setpoint", {"voltage": 12, "current": 12})
        s.call("POST", "/api/all_output", {"on": True})
        _, m = s.call("GET", "/api/measure")
        by = {c["number"]: c for c in m["channels"]}
        assert abs(by[1]["measurement"]["current"] - 2.0) < 0.3   # 24V/12ohm
        assert abs(by[2]["measurement"]["current"] - 1.0) < 0.3   # 12V/12ohm


def test_channels_are_independent():
    with _Server() as s:
        s.connect_itn6332b()
        s.call("POST", "/api/channel/2/output", {"on": True})
        _, m = s.call("GET", "/api/measure")
        by = {c["number"]: c for c in m["channels"]}
        assert by[2]["output"] is True
        assert by[1]["output"] is False


def test_per_channel_protection():
    with _Server() as s:
        s.connect_itn6332b()
        assert s.call("POST", "/api/channel/3/protection", {"ovp": 6.0})[0] == 200
        assert s.call("POST", "/api/channel/3/clear_protection", {})[0] == 200


def test_error_when_not_connected():
    with _Server() as s:
        status, body = s.call("POST", "/api/channel/1/output", {"on": True})
        assert status == 400
        assert "connect" in body["error"].lower()


def test_cpx200dp_two_channels():
    with _Server() as s:
        status, st = s.connect_cpx200dp()
        assert status == 200 and st["connected"] is True
        assert st["model"] == "cpx200dp"
        assert [c["number"] for c in st["channels"]] == [1, 2]
        for ch in st["channels"]:
            assert ch["ranges"].get("i_min", 0) == 0.0


def test_cpx200dp_multichannel_setpoint():
    with _Server() as s:
        s.connect_cpx200dp()
        s.call("POST", "/api/channel/1/setpoint", {"voltage": 12.0, "current": 3.5})
        s.call("POST", "/api/channel/2/setpoint", {"voltage": 6.0, "current": 3.5})
        s.call("POST", "/api/all_output", {"on": True})
        _, m = s.call("GET", "/api/measure")
        by = {c["number"]: c for c in m["channels"]}
        # 12 V / 12 ohm = 1 A; 6 V / 12 ohm = 0.5 A
        assert abs(by[1]["measurement"]["current"] - 1.0) < 0.3
        assert abs(by[2]["measurement"]["current"] - 0.5) < 0.3


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
