# PSU-Control — ITECH IT-N6332B SCPI control

A clean, fully type-hinted Python library **and web UI** for controlling the
**ITECH IT-N6332B** programmable DC power supply over **SCPI** via LAN, USB-TMC,
GPIB or RS-232.

The IT-N6332B uses ITECH's unified SCPI command set — the same one documented in
the **IT-M3100 / IT-N6300 Programming Guide** (included in
[`docs/`](docs/)). The supply is **bidirectional** (it can both *source* and
*sink* power), so measured current and power are **signed** — negative while
sinking. The command set and behaviour in this library are taken directly from
that programming guide; the product page is
<https://www.itechate.com/en/product/dc-power-supply/IT-N6300.html>.

## Features

- **Faithful to the programming guide** — voltage/current/power setpoints with
  the full `[SOURce:]…:LEVel:IMMediate:AMPLitude` paths, `APPLy`, CV/CC
  **priority** (`FUNCtion:PRIority`), positive/negative **slew** control, soft
  voltage limits, and `CHANnel <n>` addressing (1–16) for multi-unit systems.
- **Full protection suite** — OVP / OCP / OPP **and** under-voltage /
  under-current, with trip read-back (`STATus:QUEStionable`) and clearing
  (`OUTPut:PROTection:CLEar`).
- **Device-reported ranges** — limits are read from the instrument via SCPI
  `MIN`/`MAX` queries rather than hard-coded, so the library adapts to whatever
  model/firmware is connected.
- **Two transports, one API** — PyVISA (USB/LAN/GPIB/serial) *or* a
  dependency-free raw TCP socket backend (ITECH default port **30000**).
- **Bidirectional aware** — signed current/power; sink behaviour is first-class.
- **Safety by default** — the context manager turns the output off and releases
  remote control on exit; `check_errors()` raises on instrument-side errors.
- **Complete web dashboard** (`psu_control.web`) — live readouts, a signed
  dual-trace chart, CV/CC badge and protection controls, plus a built-in
  **simulator** so it runs with no hardware.

## Installation

```bash
pip install -r requirements.txt    # pyvisa + pyvisa-py (only needed for VISA)
```

The raw-TCP backend, CLI and web UI need **no third-party dependencies** — pure
standard library. PyVISA is only required for VISA resource strings (USB/GPIB).

## Quick start

```python
from psu_control import ITN6332B, Priority

with ITN6332B.open_tcp("192.168.1.50") as psu:
    print(psu.idn())                 # ITECH Ltd.,IT-N6332B,...
    psu.reset()

    print(psu.voltage_range())       # (0.0, 60.0)  -- queried from the device
    print(psu.current_range())       # (-12.0, 12.0) -- bidirectional

    psu.set_priority(Priority.VOLTAGE)
    psu.apply(12.0, 5.0)             # 12 V, 5 A limit
    psu.set_ovp(13.5)

    psu.output_on()
    psu.check_errors()

    print(psu.measure())             # 12.00 V, -0.30 A, -3.6 W  (sinking)
# leaving the block turns the output OFF and returns to local control
```

### Connecting via VISA (USB / GPIB / LAN)

```python
psu = ITN6332B.open_visa("USB0::0x2EC7::...::INSTR")
psu = ITN6332B.open_visa("TCPIP0::192.168.1.50::inst0::INSTR")
psu = ITN6332B.open_usb()           # auto-discover first ITECH USB device
```

### Bidirectional, slew, priority

```python
psu.set_priority(Priority.CURRENT)            # CC priority
psu.set_voltage_slew(both=10.0)               # 10 V/s ramp
psu.set_current_slew(positive=2.0, negative=1.0)
i = psu.measure_current()                     # negative value => sinking
```

### Multi-unit / channel addressing

```python
psu.select_channel(2)                # CHANnel 2 (systems of linked units)
print(psu.channel_available(2))      # CHANnel:STATe? 2
```

## Web UI

A complete browser dashboard ships in `psu_control.web` — a dependency-free
server (Python stdlib only) that wraps the driver and serves a control panel
with live readouts and a chart.

```bash
# Try it with no hardware — built-in simulator:
python -m psu_control.web --demo
# then open http://127.0.0.1:8080

# For a real instrument, start the server and connect from the UI:
python -m psu_control.web --port 8080 --host 0.0.0.0
```

The dashboard provides connection (host/port, VISA, or one-click **Demo**),
live V/I/P meters with a CV/CC badge, a real-time **signed** dual-trace chart
(so sinking is visible below the midline), an OUTPUT toggle, CV/CC-priority
voltage/current setpoints bounded by the device-reported ranges, and
OVP/OCP/OPP plus clear-trip and `*RST`.

The JSON REST API (`/api/state`, `/api/measure`, `/api/connect`,
`/api/output`, `/api/setpoint`, `/api/protection`, `/api/clear_protection`,
`/api/reset`) is documented in `psu_control/web/server.py`.

> The server binds to `127.0.0.1` by default and has no authentication. Use
> `--host 0.0.0.0` to expose it on your LAN only on a trusted network.

## Command-line interface

```bash
python -m psu_control.cli --host 192.168.1.50 idn
python -m psu_control.cli --host 192.168.1.50 measure
python -m psu_control.cli --host 192.168.1.50 set --voltage 12 --current 2 --priority cv --on
python -m psu_control.cli --host 192.168.1.50 off
```

## API overview

| Area | Methods |
|------|---------|
| Identification | `idn`, `reset`, `clear_status`, `self_test`, `wait_complete`, `firmware_version` |
| Remote/local | `remote`, `local`, `lock_local`, `beep` |
| Channel | `select_channel`, `channel`, `channel_available` |
| Priority/mode | `set_priority`, `get_priority`, `set_function_mode` (`Priority`, `FunctionMode`) |
| Voltage | `set_voltage`, `get_voltage`, `voltage_range`, `set_voltage_limits`, `set_voltage_slew` |
| Current | `set_current`, `get_current`, `current_range`, `set_current_slew` |
| Power | `set_power`, `get_power` |
| Combined | `apply` |
| Output | `output_on/off`, `set_output`, `output_enabled`, `set_output_delays` |
| Protection | `set_ovp`, `set_uvp`, `set_ocp`, `set_ucp`, `set_opp`, `clear_protection`, `questionable_condition`, `protection_tripped`, `raise_if_tripped` |
| Measure | `measure_voltage`, `measure_current`, `measure_power`, `measure`, `regulation_mode` |
| State | `save_state`, `recall_state` |
| Errors | `next_error`, `check_errors` |
| Helpers | `ramp_voltage`, `shutdown`, context manager |

Need a command not wrapped here? The raw SCPI connection is always available:

```python
psu.scpi.write("SOURce:FUNCtion:MODE BATTery")
ver = psu.scpi.query("SYSTem:COMMunicate:LAN:MACaddress?")
```

## Project layout

```
psu_control/
    __init__.py        # public exports
    it_n6332b.py       # ITN6332B driver (bidirectional, IT-M3100/IT-N6300 SCPI)
    scpi.py            # transport layer (PyVISA + raw TCP backends)
    exceptions.py      # exception hierarchy
    simulator.py       # in-process bidirectional SCPI simulator
    cli.py             # `python -m psu_control.cli`
    web/
        server.py      # stdlib HTTP backend + JSON API (`python -m psu_control.web`)
        static/        # index.html, style.css, app.js dashboard
docs/
    IT-N6332B-datasheet.pdf
    (IT-M3100 / IT-N6300 Programming Guide)
examples/
    basic_usage.py
    data_logging.py
tests/
    mock_instrument.py # shim re-exporting psu_control.simulator
    test_driver.py     # driver tests (no hardware needed)
    test_web.py        # web API tests (no hardware needed)
```

## Testing without hardware

```bash
python tests/test_driver.py        # or: python -m pytest tests/
python tests/test_web.py
```

## Notes

- Command mnemonics are taken from the IT-M3100 / IT-N6300 Programming Guide.
  If your firmware revision differs, use `psu.scpi.write/query` directly — the
  driver never hides the raw connection.
- The instrument is placed under remote control on connect. Call `psu.local()`
  (or exit the `with` block) to release the front panel.
- Always confirm wiring and limits before enabling the output. When sinking,
  ensure the connected source can safely handle the energy involved.
