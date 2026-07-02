"""Tests for the Aim-TTi CPX200DP driver, run against the in-process simulator.

Tests verify that commands are correctly formatted for the suffix-addressed
Aim-TTi ASCII command set (V1, I2, OP1, V1O?, OPALL, etc. — as documented in
the CPX200D/DP instruction manual) and that the Channel proxy works correctly
for independent dual-channel control.

Run with::

    python -m pytest tests/
    python tests/test_cpx200dp.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from psu_control.cpx200dp import CPX200DP  # noqa: E402
from psu_control.simulator import CPX200DPSimulator  # noqa: E402


def _open(sim: CPX200DPSimulator) -> CPX200DP:
    return CPX200DP.open_tcp(sim.host, sim.port)


def test_idn():
    with CPX200DPSimulator() as sim:
        psu = _open(sim)
        assert psu.idn() == CPX200DPSimulator.IDN
        psu.close()


def test_available_channels():
    with CPX200DPSimulator() as sim:
        psu = _open(sim)
        assert psu.available_channels() == [1, 2]
        psu.close()


def test_voltage_roundtrip():
    with CPX200DPSimulator() as sim:
        psu = _open(sim)
        psu.select_channel(1)
        psu.set_voltage(15.0)
        assert abs(psu.get_voltage() - 15.0) < 0.01
        assert any(r.startswith("V1 15.000") for r in sim.received)
        psu.close()


def test_current_roundtrip():
    with CPX200DPSimulator() as sim:
        psu = _open(sim)
        psu.select_channel(1)
        psu.set_current(2.5)
        assert abs(psu.get_current() - 2.5) < 0.01
        assert any(r.startswith("I1 2.500") for r in sim.received)
        psu.close()


def test_apply():
    with CPX200DPSimulator() as sim:
        psu = _open(sim)
        psu.select_channel(1)
        psu.apply(12.0, 2.0)
        assert abs(psu.get_voltage() - 12.0) < 0.01
        assert abs(psu.get_current() - 2.0) < 0.01
        psu.close()


def test_output_control():
    with CPX200DPSimulator() as sim:
        psu = _open(sim)
        psu.select_channel(1)
        assert psu.output_enabled is False
        psu.output_on()
        assert psu.output_enabled is True
        psu.output_off()
        assert psu.output_enabled is False
        psu.close()


def test_measure_cv():
    with CPX200DPSimulator() as sim:
        psu = _open(sim)
        psu.select_channel(1)
        psu.apply(12.0, 3.5)          # 12 V into 12 ohm -> 1 A (CV)
        psu.output_on()
        m = psu.measure()
        assert abs(m.voltage - 12.0) < 0.1
        assert abs(m.current - 1.0) < 0.1
        assert abs(m.power - 12.0) < 0.5
        psu.close()


def test_measure_cc_limiting():
    with CPX200DPSimulator() as sim:
        psu = _open(sim)
        psu.select_channel(1)
        psu.apply(24.0, 1.0)          # would draw 2 A, limited to 1 A -> CC
        psu.output_on()
        assert abs(psu.measure_current() - 1.0) < 0.1
        assert psu.regulation_mode() == "CC"
        psu.close()


def test_voltage_range():
    with CPX200DPSimulator() as sim:
        psu = _open(sim)
        lo, hi = psu.voltage_range()
        assert lo == 0.0
        assert hi == CPX200DP.VOLTAGE_MAX
        psu.close()


def test_current_range_source_only():
    with CPX200DPSimulator() as sim:
        psu = _open(sim)
        lo, hi = psu.current_range()
        assert lo == 0.0              # source-only: min is 0, not negative
        assert hi == CPX200DP.CURRENT_MAX
        psu.close()


def test_channel_proxy_suffix_addressing():
    """Each Channel proxy targets the correct suffix (V1 vs V2)."""
    with CPX200DPSimulator() as sim:
        psu = _open(sim)
        psu.channel(1).set_voltage(10.0)
        psu.channel(2).set_voltage(20.0)
        assert abs(psu.channel(1).get_voltage() - 10.0) < 0.01
        assert abs(psu.channel(2).get_voltage() - 20.0) < 0.01
        assert any(r.startswith("V1 10.000") for r in sim.received)
        assert any(r.startswith("V2 20.000") for r in sim.received)
        psu.close()


def test_all_output_uses_opall():
    """all_output_on/off with no subset must use the simultaneous OPALL command."""
    with CPX200DPSimulator() as sim:
        psu = _open(sim)
        psu.all_output_on()
        assert psu.channel(1).output_enabled and psu.channel(2).output_enabled
        psu.all_output_off()
        assert not psu.channel(1).output_enabled and not psu.channel(2).output_enabled
        assert any(r.startswith("OPALL 1") for r in sim.received)
        assert any(r.startswith("OPALL 0") for r in sim.received)
        psu.close()


def test_multichannel_independent_control():
    with CPX200DPSimulator() as sim:
        psu = _open(sim)
        psu.channel(1).apply(12.0, 3.5)  # 12 V / 12 ohm -> 1 A
        psu.channel(2).apply(6.0, 3.5)   # 6 V / 12 ohm -> 0.5 A
        psu.all_output_on()
        snap = psu.measure_all()
        assert set(snap) == {1, 2}
        assert abs(snap[1].voltage - 12.0) < 0.1
        assert abs(snap[2].voltage - 6.0) < 0.1
        assert abs(snap[1].current - 1.0) < 0.1
        assert abs(snap[2].current - 0.5) < 0.1
        psu.close()


def test_channels_are_independent():
    with CPX200DPSimulator() as sim:
        psu = _open(sim)
        psu.channel(2).output_on()
        assert psu.channel(2).output_enabled is True
        assert psu.channel(1).output_enabled is False
        psu.close()


def test_protection_ovp_and_clear():
    with CPX200DPSimulator() as sim:
        psu = _open(sim)
        psu.channel(1).set_ovp(13.0)
        # Manually trip the channel so clear is exercised.
        sim.channels[1]["tripped"] = True
        assert psu.channel(1).protection_tripped() is True
        psu.channel(1).clear_protection()
        assert psu.channel(1).protection_tripped() is False
        psu.close()


def test_get_priority_always_voltage():
    """CPX200DP is always CV; get_priority returns 'VOLTAGE'."""
    with CPX200DPSimulator() as sim:
        psu = _open(sim)
        assert psu.get_priority() == "VOLTAGE"
        assert psu.channel(1).get_priority() == "VOLTAGE"
        psu.close()


def test_context_manager_shuts_down():
    with CPX200DPSimulator() as sim:
        with _open(sim) as psu:
            psu.channel(1).output_on()
            psu.channel(2).output_on()
        # Outputs must be off after the with block.
        deadline = time.monotonic() + 1.0
        while any(c["output"] for c in sim.channels.values()) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not any(c["output"] for c in sim.channels.values())


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
