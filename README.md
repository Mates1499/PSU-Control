# PSU-Control — multi-model programmable PSU control

A clean, fully type-hinted Python library **and web UI** for controlling
programmable DC power supplies over their remote command sets.

Supported instruments (manuals in [`docs/`](docs/)):

| Model | Channels | Command set | Default port |
|---|---|---|---|
| **ITECH IT-N6332B** (IT-N6300 series) | 3, bidirectional | SCPI (short-form) | TCP 30000 |
| **Aim-TTi CPX200DP** | 2, source-only | Aim-TTi ASCII (`V1`, `OP1`, …) | TCP 9221 |

The IT-N6332B is **bidirectional** (it can both *source* and *sink* power), so
measured current and power are **signed** — negative while sinking. The ITECH
factory-default LAN address is **192.168.200.100**.

## SCPI commands used (IT-N6332B)

The driver sends the short-form IT-N6300 command set — the long
`SOURce:VOLTage:LEVel:IMMediate:AMPLitude` style paths raise
**error 150 ("wrong parameter")** on this instrument:

```
INST:NSEL 1            # select channel (Extended mode; CHAN 1 in Standard mode)
VOLT 5.0               # set voltage on the selected channel
CURR 1.0               # set current limit
OUTP ON / OUTP OFF     # output enable — selected channel only
VOLT? / CURR?          # read setpoints
MEAS:VOLT? / MEAS:CURR?  # measure actual output
SYST:ERR?              # drain the error queue (checked after writes)
```

### Channel-select auto-detection

The instrument has two remote command modes (front panel *System →
Instructions*): **Standard** (IT-N6300 native, `CHANnel <n>`) and **Extended**
(IT6300-compatible, `INST:NSEL <n>`). On connect the driver probes which form
the firmware accepts (verifying with `SYST:ERR?`) and uses it from then on, so
it works in either mode with no configuration.

### Per-channel output

`OUTP ON`/`OFF` acts on the **currently selected channel**; the driver
re-asserts the selection before every command, so toggling one channel never
touches the others.

> **If all three outputs still switch together**, the instrument itself is
> ganging them: on the front panel set *System → Output Coupling* to **Off**
> (not ALL / CH1-CH2 / …) and *Coupling* ([Shift]+[7]) to **Standard**. These
> hardware coupling modes cannot be overridden over SCPI.

## Features

- **Multi-model** — one `BasePSUDriver` interface; the CLI, web UI and
  `Channel` proxy work unchanged with every supported instrument.
- **Device-reported ranges** (ITECH) — limits are read from the instrument via
  SCPI `MIN`/`MAX` queries; the CPX200DP uses its rated 60 V / 10 A limits.
- **Full protection suite** (ITECH) — OVP / OCP / OPP and under-voltage /
  under-current, with trip read-back and clearing. The CPX200DP maps OVP/OCP,
  `TRIPRST` and the `LSR` trip registers.
- **Two transports, one API** — PyVISA (USB/LAN/GPIB/serial) *or* a
  dependency-free raw TCP socket backend.
- **Bidirectional aware** — signed current/power; sink behaviour is first-class.
- **Safety by default** — the context manager turns outputs off on exit;
  `check_errors()` raises on instrument-side errors; the web UI clamps
  setpoints to each channel's rated range before sending.
- **Web dashboard** (`psu_control.web`) — live per-channel readouts at 500 ms,
  a signed dual-trace chart, CV/CC/OFF badge, per-channel output toggle and
  protection controls. **Requires a real instrument — there is no simulation
  mode** (in-process simulators remain available for the test suite).

## Installation

```bash
pip install -r requirements.txt    # pyvisa + pyvisa-py (only needed for VISA)
```

The raw-TCP backend, CLI and web UI need **no third-party dependencies** — pure
standard library. PyVISA is only required for VISA resource strings (USB/GPIB).

## Quick start

```python
from psu_control import ITN6332B, Priority

with ITN6332B.open_tcp("192.168.200.100") as psu:
    print(psu.idn())                 # ITECH Ltd.,IT-N6332B,...

    print(psu.voltage_range())       # (0.0, 60.0)  -- queried from the device
    print(psu.current_range())       # (-12.0, 12.0) -- bidirectional

    psu.set_priority(Priority.VOLTAGE)
    psu.apply(1.0, 0.5)              # keep test voltages low for safety
    psu.set_ovp(13.5)

    psu.output_on()
    psu.check_errors()               # raises if SYST:ERR? reports anything

    print(psu.measure())
# leaving the block turns the output OFF and returns to local control
```

### Aim-TTi CPX200DP

```python
from psu_control import CPX200DP

with CPX200DP.open_tcp("192.168.200.101") as psu:
    print(psu.idn())
    psu.channel(1).apply(12.0, 2.0)  # V1 12.000 / I1 2.000
    psu.channel(2).apply(5.0, 1.0)
    psu.all_output_on()              # OPALL 1 — simultaneous
    for n, m in psu.measure_all().items():
        print(f"CH{n}: {m}")         # V1O? / I1O? readbacks
```

### Connecting via VISA (USB / GPIB / LAN)

```python
psu = ITN6332B.open_visa("USB0::0x2EC7::...::INSTR")
psu = ITN6332B.open_visa("TCPIP0::192.168.200.100::inst0::INSTR")
psu = ITN6332B.open_usb()           # auto-discover first ITECH USB device
```

### Multiple channels

Use the `Channel` proxy from `psu.channel(n)` to drive each output
independently — selection is handled for you:

```python
print(psu.available_channels())      # [1, 2, 3]

psu.channel(1).apply(12.0, 5.0)      # each call selects its channel first
psu.channel(2).apply(5.0, 1.0)
psu.channel(1).output_on()           # only CH1 switches on
psu.channel(2).set_priority(Priority.CURRENT)

for n, m in psu.measure_all().items():
    print(f"CH{n}: {m}")

psu.all_output_on()                  # or all_output_off()
```

A `Channel` exposes the same channel-scoped methods as the driver
(`set_voltage`, `apply`, `set_ovp`, `measure`, `output_enabled`, …) plus
`.available`.

## Web UI

A complete browser dashboard ships in `psu_control.web` — a dependency-free
server (Python stdlib only) that wraps the driver and serves a control panel
with live readouts and a chart.

```bash
python -m psu_control.web --port 8080
# then open http://127.0.0.1:8080 and connect to the instrument's IP
```

The dashboard provides connection setup (model, host/port or VISA), **one card
per channel — all three IT-N6332B channels are shown and refreshed live every
500 ms**, each with V/I/P meters, a CV/CC/OFF badge, a signed dual-trace chart
(sinking is visible below the midline), a per-channel OUTPUT toggle, setpoints
validated and clamped against the channel's rated range, and OVP/OCP/OPP with
clear-trip. Master controls toggle all outputs and send `*RST`.

If the instrument cannot be reached, connecting fails with a clear error —
there is no demo fallback.

The JSON REST API (`/api/state`, `/api/measure`, `/api/connect`,
`/api/all_output`, `/api/reset`, and per-channel
`/api/channel/<n>/{setpoint,output,protection,clear_protection}`) is documented
in `psu_control/web/server.py`.

> The server binds to `127.0.0.1` by default and has no authentication. Use
> `--host 0.0.0.0` to expose it on your LAN only on a trusted network.

## Command-line interface

```bash
python -m psu_control.cli idn                                   # default host 192.168.200.100
python -m psu_control.cli channels                              # list channels
python -m psu_control.cli measure                               # all channels
python -m psu_control.cli --channel 2 set --voltage 12 --current 2 --priority cv --on
python -m psu_control.cli --all off                             # every channel
python -m psu_control.cli --model cpx200dp --host 192.168.200.101 measure
```

## API overview

| Area | Methods |
|------|---------|
| Identification | `idn`, `reset`, `clear_status`, `self_test`, `wait_complete`, `firmware_version` |
| Remote/local | `remote`, `local`, `lock_local`, `beep` |
| Channels | `channel(n)` → `Channel` proxy, `channels`, `available_channels`, `all_output_on/off`, `measure_all`, `select_channel`, `selected_channel`, `channel_available` |
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

Need a command not wrapped here? The raw connection is always available:

```python
psu.scpi.write("SOURce:FUNCtion:MODE BATTery")
ver = psu.scpi.query("SYSTem:COMMunicate:LAN:MACaddress?")
```

## Project layout

```
psu_control/
    __init__.py        # public exports
    base.py            # BasePSUDriver ABC + Channel proxy + Measurement
    it_n6332b.py       # ITECH IT-N6332B driver (short-form IT-N6300 SCPI)
    cpx200dp.py        # Aim-TTi CPX200DP driver (V1/I1/OP1 ASCII set)
    scpi.py            # transport layer (PyVISA + raw TCP backends)
    exceptions.py      # exception hierarchy
    simulator.py       # in-process simulators (used by the test suite)
    cli.py             # `python -m psu_control.cli`
    web/
        server.py      # stdlib HTTP backend + JSON API (`python -m psu_control.web`)
        static/        # index.html, style.css, app.js dashboard
docs/
    IT-N6332B-datasheet.pdf
    IT-M3100-IT-N6300-programming-guide.pdf
    IT-N6300 User Manual-EN.pdf
    CPX200D+DP_Instruction_Manual-Iss8.pdf
examples/
    basic_usage.py
    data_logging.py
tests/
    test_driver.py     # IT-N6332B driver tests (no hardware needed)
    test_cpx200dp.py   # CPX200DP driver tests (no hardware needed)
    test_web.py        # web API tests (no hardware needed)
```

## Testing without hardware

```bash
python -m pytest tests/
```

The tests spin up in-process simulators that speak each instrument's real
command dialect (including the ITECH Standard/Extended channel-select modes
and the CPX200DP's prefixed/unit-suffixed replies).

## Notes

- Command mnemonics are taken from the manuals in `docs/`. If your firmware
  revision differs, use `psu.scpi.write/query` directly — the driver never
  hides the raw connection.
- The ITECH instrument is placed under remote control on connect. Call
  `psu.local()` (or exit the `with` block) to release the front panel.
- Always confirm wiring and limits before enabling the output, and keep test
  voltages low. When sinking, ensure the connected source can safely handle
  the energy involved.
