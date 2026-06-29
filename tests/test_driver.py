"""Tests for the IT-N6332B driver, run against the in-process mock instrument.

These verify SCPI command formatting and the driver's read-back parsing without
needing real hardware. Run with::

    python -m pytest tests/
    # or, without pytest installed:
    python tests/test_driver.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from psu_control import ITN6332B, OutputMode  # noqa: E402
from tests.mock_instrument import MockInstrument  # noqa: E402


def _open(mock: MockInstrument) -> ITN6332B:
    # claim_remote=False keeps the received-command log focused on the test.
    return ITN6332B.open_tcp(mock.host, mock.port, claim_remote=False)


def test_idn():
    with MockInstrument() as mock:
        psu = _open(mock)
        assert psu.idn() == MockInstrument.IDN
        psu.close()


def test_voltage_roundtrip():
    with MockInstrument() as mock:
        psu = _open(mock)
        psu.set_voltage(12.5)
        assert abs(psu.get_voltage() - 12.5) < 1e-6
        assert "SOURce:VOLTage:LEVel:IMMediate:AMPLitude 12.5" in mock.received
        psu.close()


def test_symmetric_current_limit():
    with MockInstrument() as mock:
        psu = _open(mock)
        psu.set_current_limit(3.0)
        pos, neg = psu.get_current_limits()
        assert abs(pos - 3.0) < 1e-6
        assert abs(neg + 3.0) < 1e-6
        psu.close()


def test_asymmetric_current_limit():
    with MockInstrument() as mock:
        psu = _open(mock)
        psu.set_current_limit(positive=4.0, negative=1.5)
        pos, neg = psu.get_current_limits()
        assert abs(pos - 4.0) < 1e-6
        assert abs(neg + 1.5) < 1e-6  # stored as negative
        psu.close()


def test_output_and_measure():
    with MockInstrument() as mock:
        psu = _open(mock)
        psu.set_voltage(12.0)
        assert psu.output_enabled is False
        psu.output_on()
        assert psu.output_enabled is True
        m = psu.measure_all()
        assert abs(m.voltage - 12.0) < 1e-6
        assert abs(m.current - 2.0) < 1e-6   # 12V / 6ohm
        assert abs(m.power - 24.0) < 1e-6
        psu.close()


def test_mode_select():
    with MockInstrument() as mock:
        psu = _open(mock)
        psu.set_mode(OutputMode.CURRENT)
        assert psu.get_mode() is OutputMode.CURRENT
        psu.set_mode(OutputMode.VOLTAGE)
        assert psu.get_mode() is OutputMode.VOLTAGE
        psu.close()


def test_protections_set_and_status():
    with MockInstrument() as mock:
        psu = _open(mock)
        psu.set_ovp(13.5)
        psu.set_ocp(2.5)
        psu.set_opp(30.0)
        status = psu.protection_status()
        assert status.any_tripped is False
        # Simulate an OVP trip and confirm decoding.
        mock.state["ques"] = 0x0001
        assert psu.protection_status().ovp is True
        psu.close()


def test_apply_helper():
    with MockInstrument() as mock:
        psu = _open(mock)
        psu.apply(voltage=5.0, current_limit=1.0)
        assert psu.output_enabled is True
        assert abs(psu.get_voltage() - 5.0) < 1e-6
        psu.close()


def test_context_manager_fails_safe():
    with MockInstrument() as mock:
        with _open(mock) as psu:
            psu.output_on()
            assert psu.output_enabled is True
        # After the block, output should have been turned off. The OFF write is
        # async over the socket, so wait briefly for the mock to process it.
        deadline = time.monotonic() + 1.0
        while mock.state["output"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert mock.state["output"] is False


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
