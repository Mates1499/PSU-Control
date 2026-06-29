"""Tests for the triple-channel IT-N6332B driver, run against the simulator.

These verify the SCPI channel-selection handshake, command formatting and
read-back parsing without needing real hardware. Run with::

    python -m pytest tests/
    python tests/test_driver.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from psu_control import ITN6332B  # noqa: E402
from tests.mock_instrument import MockInstrument  # noqa: E402


def _open(mock: MockInstrument) -> ITN6332B:
    return ITN6332B.open_tcp(mock.host, mock.port, claim_remote=False)


def test_idn():
    with MockInstrument() as mock:
        psu = _open(mock)
        assert psu.idn() == MockInstrument.IDN
        psu.close()


def test_channels_have_correct_specs():
    with MockInstrument() as mock:
        psu = _open(mock)
        assert (psu.ch1.spec.max_voltage, psu.ch1.spec.max_current) == (30.0, 6.0)
        assert (psu.ch2.spec.max_voltage, psu.ch2.spec.max_current) == (30.0, 6.0)
        assert (psu.ch3.spec.max_voltage, psu.ch3.spec.max_current) == (5.0, 3.0)
        psu.close()


def test_voltage_roundtrip_per_channel():
    with MockInstrument() as mock:
        psu = _open(mock)
        psu.ch1.set_voltage(12.5)
        psu.ch3.set_voltage(3.3)
        assert abs(psu.ch1.get_voltage() - 12.5) < 1e-4
        assert abs(psu.ch3.get_voltage() - 3.3) < 1e-4
        # Selecting different channels must emit INSTrument:NSELect.
        assert any("NSEL" in c.upper() for c in mock.received)
        psu.close()


def test_channel_selection_is_cached():
    with MockInstrument() as mock:
        psu = _open(mock)
        psu.ch1.set_voltage(1.0)
        psu.ch1.set_voltage(2.0)
        psu.ch1.set_voltage(3.0)
        # A query round-trips, guaranteeing the async writes above were received
        # and recorded before we inspect the log (TCP preserves ordering).
        psu.ch1.get_voltage()
        # Three writes to the same channel -> only one NSELect.
        nsel = [c for c in mock.received if "NSEL" in c.upper()]
        assert len(nsel) == 1
        psu.close()


def test_range_validation():
    with MockInstrument() as mock:
        psu = _open(mock)
        # CH3 maxes at 5 V; 12 V must be rejected before hitting the wire.
        try:
            psu.ch3.set_voltage(12.0)
            assert False, "expected ValueError"
        except ValueError:
            pass
        psu.close()


def test_apply_and_measure():
    with MockInstrument() as mock:
        psu = _open(mock)
        psu.ch1.apply(30.0, 6.0)   # 30 V into a 30 ohm load -> ~1 A
        psu.ch1.output_on()
        m = psu.ch1.measure()
        assert abs(m.voltage - 30.0) < 1e-4
        assert abs(m.current - 1.0) < 1e-4
        assert abs(m.power - 30.0) < 1e-4
        psu.close()


def test_constant_current_limit():
    with MockInstrument() as mock:
        psu = _open(mock)
        # 30 V into 30 ohm would be 1 A, but limit at 0.5 A forces CC.
        psu.ch1.apply(30.0, 0.5)
        psu.ch1.output_on()
        assert psu.ch1.regulation_mode() == "CC"
        assert abs(psu.ch1.measure_current() - 0.5) < 1e-4
        psu.close()


def test_all_outputs():
    with MockInstrument() as mock:
        psu = _open(mock)
        psu.all_output_on()
        assert all(ch.output_enabled for ch in psu.channels)
        psu.all_output_off()
        assert not any(ch.output_enabled for ch in psu.channels)
        psu.close()


def test_measure_all():
    with MockInstrument() as mock:
        psu = _open(mock)
        psu.ch1.apply(12.0, 6.0)
        psu.ch1.output_on()
        snap = psu.measure_all()
        assert set(snap) == {"CH1", "CH2", "CH3"}
        assert abs(snap["CH1"].voltage - 12.0) < 1e-4
        psu.close()


def test_context_manager_fails_safe():
    with MockInstrument() as mock:
        with _open(mock) as psu:
            psu.ch1.output_on()
            psu.ch2.output_on()
            assert psu.ch1.output_enabled
        deadline = time.monotonic() + 1.0
        while any(c["output"] for c in mock.channels.values()) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not any(c["output"] for c in mock.channels.values())


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
