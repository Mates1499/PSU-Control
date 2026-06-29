"""Tests for the IT-N6332B driver, run against the in-process simulator.

These verify SCPI command formatting and read-back parsing for the bidirectional
IT-M3100/IT-N6300 command set, without needing real hardware. Run with::

    python -m pytest tests/
    python tests/test_driver.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from psu_control import ITN6332B, Priority  # noqa: E402
from tests.mock_instrument import MockInstrument  # noqa: E402


def _open(mock: MockInstrument) -> ITN6332B:
    return ITN6332B.open_tcp(mock.host, mock.port, claim_remote=False)


def test_idn():
    with MockInstrument() as mock:
        psu = _open(mock)
        assert psu.idn() == MockInstrument.IDN
        psu.close()


def test_selects_channel_on_open():
    with MockInstrument() as mock:
        psu = _open(mock)
        psu.idn()  # round-trip flush so the async CHANnel write is recorded
        assert psu.selected_channel == 1
        assert any(c.upper().startswith("CHAN") for c in mock.received)
        psu.close()


def test_voltage_roundtrip():
    with MockInstrument() as mock:
        psu = _open(mock)
        psu.set_voltage(12.5)
        assert abs(psu.get_voltage() - 12.5) < 1e-3
        assert "SOURce:VOLTage:LEVel:IMMediate:AMPLitude 12.5" in mock.received
        psu.close()


def test_priority_roundtrip():
    with MockInstrument() as mock:
        psu = _open(mock)
        psu.set_priority(Priority.CURRENT)
        assert psu.get_priority() == "CURRENT"
        psu.set_priority(Priority.VOLTAGE)
        assert psu.get_priority() == "VOLTAGE"
        # set_priority also accepts plain strings
        psu.set_priority("cc")
        assert psu.get_priority() == "CURRENT"
        psu.close()


def test_apply_and_measure():
    with MockInstrument() as mock:
        psu = _open(mock)
        psu.apply(24.0, 12.0)        # 24 V into 12 ohm -> 2 A
        psu.output_on()
        m = psu.measure()
        assert abs(m.voltage - 24.0) < 1e-3
        assert abs(m.current - 2.0) < 1e-3
        assert abs(m.power - 48.0) < 1e-3
        psu.close()


def test_constant_current_limit():
    with MockInstrument() as mock:
        psu = _open(mock)
        psu.apply(24.0, 1.0)         # would be 2 A, limited to 1 A -> CC
        psu.output_on()
        assert psu.regulation_mode() == "CC"
        assert abs(psu.measure_current() - 1.0) < 1e-3
        psu.close()


def test_device_reported_ranges():
    with MockInstrument() as mock:
        psu = _open(mock)
        vlo, vhi = psu.voltage_range()
        ilo, ihi = psu.current_range()
        assert (vlo, vhi) == (0.0, 60.0)
        assert ilo < 0 < ihi          # bidirectional current range
        # the setpoint must be restored after MIN/MAX probing
        psu.set_voltage(7.0)
        psu.voltage_range()
        assert abs(psu.get_voltage() - 7.0) < 1e-3
        psu.close()


def test_protection_set_and_clear():
    with MockInstrument() as mock:
        psu = _open(mock)
        psu.set_ovp(13.5)
        psu.set_ocp(2.5)
        psu.set_opp(60.0)
        assert psu.protection_tripped() is False
        mock.channels[1]["ques"] = 0x0001
        assert psu.protection_tripped() is True
        psu.clear_protection()
        assert psu.protection_tripped() is False
        psu.close()


def test_output_toggle():
    with MockInstrument() as mock:
        psu = _open(mock)
        assert psu.output_enabled is False
        psu.output_on()
        assert psu.output_enabled is True
        psu.output_off()
        assert psu.output_enabled is False
        psu.close()


def test_multichannel_independent_control():
    with MockInstrument(channels=3) as mock:
        psu = _open(mock)
        assert psu.available_channels() == [1, 2, 3]
        psu.channel(1).apply(24.0, 12.0)   # 24 V / 12 ohm -> 2 A
        psu.channel(2).apply(12.0, 12.0)   # 12 V / 12 ohm -> 1 A
        psu.channel(3).apply(6.0, 12.0)    # 6 V / 12 ohm -> 0.5 A
        psu.all_output_on()
        snap = psu.measure_all()
        assert set(snap) == {1, 2, 3}
        assert abs(snap[1].voltage - 24.0) < 1e-3
        assert abs(snap[2].voltage - 12.0) < 1e-3
        assert abs(snap[3].current - 0.5) < 1e-3
        psu.close()


def test_channel_proxy_selection_and_availability():
    with MockInstrument(channels=2) as mock:
        psu = _open(mock)
        assert psu.available_channels() == [1, 2]      # only 2 present
        assert psu.channel(2).available is True
        assert psu.channel(1).output_enabled is False
        psu.channel(2).output_on()
        assert psu.channel(2).output_enabled is True
        assert psu.channel(1).output_enabled is False  # independent
        psu.close()


def test_context_manager_fails_safe():
    with MockInstrument() as mock:
        with _open(mock) as psu:
            psu.output_on()
            assert psu.output_enabled is True
        # On exit shutdown() turns every channel off; wait for the async writes.
        deadline = time.monotonic() + 1.0
        while any(c["output"] for c in mock.channels.values()) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not any(c["output"] for c in mock.channels.values())


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
