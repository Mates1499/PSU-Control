# PSU-Control — ITECH IT-N6332B SCPI control

A clean, fully type-hinted Python library for completely controlling the
**ITECH IT-N6332B** bidirectional (two-quadrant) programmable DC power supply
over **SCPI** via LAN, USB-TMC, GPIB or serial.

The IT-N6332B (part of the IT-N6300 series) can both **source** power (act as a
PSU) and **sink** power (act as an electronic load), so current and power limits
are defined separately for the positive (sourcing) and negative (sinking)
directions. This library exposes all of that through an ergonomic API.

## Features

- **Two transports, one API** — PyVISA (USB/LAN/GPIB/serial) *or* a
  dependency-free raw TCP socket backend (ITECH port 30000).
- **Full control surface** — output on/off & sequencing delays, CV/CC priority
  modes, voltage/current/power setpoints, hardware slew rates, and independent
  positive/negative current & power limits.
- **Complete protection suite** — OVP / OCP / OPP configuration plus decoded
  trip status (incl. over-temperature read-back) and protection clearing.
- **Measurements** — instantaneous V / I / P, individually or as a snapshot.
- **Safety by default** — the context manager turns the output off and releases
  remote control on exit; an error-queue helper raises on instrument-side errors.
- **Convenience** — one-call `apply()`, software voltage ramping, saved-state
  recall, a `python -m psu_control.cli` command line, and a built-in mock
  instrument for tests / dry runs without hardware.

## Installation

```bash
pip install -r requirements.txt    # pyvisa + pyvisa-py
```

The raw-TCP backend needs **no dependencies at all** — pure standard library.
PyVISA is only required if you connect via VISA resource strings (USB/GPIB).

## Quick start

```python
from psu_control import ITN6332B, OutputMode

# Connect over the network (no VISA required):
with ITN6332B.open_tcp("192.168.1.50") as psu:
    print(psu.idn())                 # ITECH Ltd.,IT-N6332B,...
    psu.reset()

    psu.set_mode(OutputMode.VOLTAGE) # constant-voltage priority
    psu.set_voltage(12.0)            # 12 V setpoint
    psu.set_current_limit(2.0)       # symmetric +/-2 A (source & sink)

    psu.set_ovp(13.5)                # arm protections
    psu.set_ocp(2.5)

    psu.output_on()
    psu.check_errors()               # raise if the instrument rejected anything

    print(psu.measure_all())         # 12.00 V, 1.99 A, 23.9 W
    print(psu.protection_status())   # OK (no protection tripped)
# leaving the block turns the output OFF and returns to local control
```

### Connecting via VISA (USB / GPIB / LAN)

```python
psu = ITN6332B.open_visa("USB0::0x2EC7::0x6300::800001::INSTR")
psu = ITN6332B.open_visa("TCPIP0::192.168.1.50::inst0::INSTR")
psu = ITN6332B.open_usb()           # auto-discover first ITECH USB device
```

### Bidirectional limits

```python
# Source up to 4 A, sink up to 1.5 A:
psu.set_current_limit(positive=4.0, negative=1.5)
psu.set_power_limits(positive=200.0, negative=100.0)

i = psu.measure_current()           # negative value means the supply is sinking
```

## Web UI

A complete browser dashboard ships in `psu_control.web` — a dependency-free
server (Python stdlib only) that wraps the driver and serves a single-page
control panel with live readouts and a chart.

```bash
# Try it with no hardware — built-in simulator:
python -m psu_control.web --demo
# Then open http://127.0.0.1:8080 and click "Demo (simulator)" if not already connected.

# For a real instrument, just start the server and connect from the UI:
python -m psu_control.web --port 8080 --host 0.0.0.0
```

The dashboard provides:

- **Connection panel** — connect by host/port (raw TCP) or VISA resource, plus a
  one-click **Demo** mode backed by the in-process simulator.
- **Live measurements** — V / I / P meters polled at ~2 Hz with a real-time
  dual-trace (voltage + current) canvas chart.
- **Output control** — a large ON/OFF toggle that reflects the instrument state.
- **Setpoints** — CV/CC mode, voltage and symmetric current limit, applied in one click.
- **Protection** — set OVP / OCP / OPP, view decoded trip status, clear a latched
  trip, and send `*RST`.

It is a thin REST layer over the same driver — the JSON API
(`/api/state`, `/api/measure`, `/api/connect`, `/api/output`, `/api/setpoint`,
`/api/protection`, `/api/clear_protection`, `/api/reset`) is documented in
`psu_control/web/server.py` and is easy to script against directly.

> The server binds to `127.0.0.1` by default. Use `--host 0.0.0.0` to expose it
> on your LAN; it has no authentication, so only do that on a trusted network.

## Command-line interface

```bash
python -m psu_control.cli --host 192.168.1.50 idn
python -m psu_control.cli --host 192.168.1.50 set --voltage 12 --current 2 --ovp 13.5 --on
python -m psu_control.cli --host 192.168.1.50 measure
python -m psu_control.cli --host 192.168.1.50 status
python -m psu_control.cli --host 192.168.1.50 off
```

## API overview

| Area | Methods |
|------|---------|
| Identification | `idn`, `reset`, `clear_status`, `self_test`, `wait_complete` |
| Remote/local | `remote`, `local`, `beep` |
| Output | `output_on`, `output_off`, `set_output`, `output_enabled`, `set_output_delays` |
| Mode | `set_mode`, `get_mode` (`OutputMode.VOLTAGE` / `CURRENT`) |
| Voltage | `set_voltage`, `get_voltage`, `set_voltage_slew`, `set_voltage_limits` |
| Current | `set_current`, `set_current_limit`, `get_current_limits`, `set_current_slew` |
| Power | `set_power_limits` |
| Protection | `set_ovp`, `set_ocp`, `set_opp`, `protection_status`, `clear_protection`, `raise_if_tripped` |
| Measure | `measure_voltage`, `measure_current`, `measure_power`, `measure_all` |
| State | `save_state`, `recall_state` |
| Errors | `next_error`, `check_errors` |
| Helpers | `apply`, `ramp_voltage`, `shutdown` |

Need a command not wrapped here? The raw SCPI connection is always available:

```python
psu.scpi.write("SYSTem:BEEPer:STATe ON")
fw = psu.scpi.query("SYSTem:VERSion?")
```

## Project layout

```
psu_control/
    __init__.py        # public exports
    it_n6332b.py       # ITN6332B high-level driver
    scpi.py            # transport layer (PyVISA + raw TCP backends)
    exceptions.py      # exception hierarchy
    simulator.py       # in-process SCPI simulator (tests + web demo)
    cli.py             # `python -m psu_control.cli`
    web/
        server.py      # stdlib HTTP backend + JSON API (`python -m psu_control.web`)
        static/        # index.html, style.css, app.js dashboard
examples/
    basic_usage.py
    data_logging.py
tests/
    mock_instrument.py # shim re-exporting psu_control.simulator
    test_driver.py     # driver + web API tests (no hardware needed)
```

## Testing without hardware

A self-contained mock instrument lets the whole stack run with no PSU attached:

```bash
python tests/test_driver.py        # or: python -m pytest tests/
```

## Notes

- SCPI mnemonics follow the IT-N6300 series programming reference. If your
  firmware revision uses a slightly different mnemonic, use `psu.scpi.write/query`
  directly — the driver never hides the raw connection.
- The instrument is placed under remote control on connect (the front panel
  locks). Call `psu.local()` (or just exit the `with` block) to release it.
- Always confirm wiring and limits before enabling the output. When sinking,
  ensure your source can safely absorb / supply the current involved.
