# PSU-Control — ITECH IT-N6332B SCPI control

A clean, fully type-hinted Python library **and web UI** for controlling the
**ITECH IT-N6332B** triple-channel programmable DC power supply over **SCPI**
via LAN, USB-TMC, GPIB or RS-232.

The IT-N6332B (ITECH's IT-N6300 / IT6300 family, model line IT6322/IT6332/IT6333)
provides **three independent, isolated output channels** that can also be
combined in series/parallel tracking:

| Channel | Voltage   | Current  | Power |
|---------|-----------|----------|-------|
| CH1     | 0 – 30 V  | 0 – 6 A  | 180 W |
| CH2     | 0 – 30 V  | 0 – 6 A  | 180 W |
| CH3     | 0 – 5 V   | 0 – 3 A  | 15 W  |

Setpoint resolution is 1 mV / 1 mA. The datasheet for this unit is in the repo
(`docs/`), and the product page is
<https://www.itechate.com/en/product/dc-power-supply/IT-N6300.html>.

> **Note on sources:** this unit is *source-only* and *triple-channel* — it is
> not a bidirectional/two-quadrant supply. Earlier revisions of this library
> incorrectly modelled it as bidirectional; v2 corrects the whole architecture.

## Features

- **Per-channel API** — `psu.ch1`, `psu.ch2`, `psu.ch3` (or `psu.channel(n)`),
  each with voltage/current setpoints, `apply()`, output control, OVP, and
  measurements. The `INSTrument` channel-selection handshake is fully hidden
  (and cached, so repeated commands on one channel don't re-select it).
- **Range validation** — setpoints are checked against each channel's factory
  ratings before they ever hit the wire (e.g. CH3 rejects > 5 V).
- **Two transports, one API** — PyVISA (USB/LAN/GPIB/serial) *or* a
  dependency-free raw TCP socket backend (ITECH port 30000).
- **Master operations** — all-on/all-off, series/parallel tracking, `*RST`,
  save/recall, error-queue checking.
- **Safety by default** — the context manager turns every output off and
  releases remote control on exit.
- **Complete web dashboard** (`psu_control.web`) — a per-channel control panel
  with live readouts and charts, plus a built-in **simulator** so it runs with
  no hardware.

## Installation

```bash
pip install -r requirements.txt    # pyvisa + pyvisa-py (only needed for VISA)
```

The raw-TCP backend and the web UI need **no third-party dependencies** — pure
standard library. PyVISA is only required for VISA resource strings (USB/GPIB).

## Quick start

```python
from psu_control import ITN6332B

with ITN6332B.open_tcp("192.168.1.50") as psu:
    print(psu.idn())                 # ITECH Ltd.,IT-N6332B,...
    psu.reset()

    psu.ch1.apply(12.0, 2.0)         # CH1: 12 V, 2 A limit
    psu.ch3.apply(3.3, 0.5)          # CH3: 3.3 V, 0.5 A limit
    psu.ch1.set_ovp(13.5)            # arm over-voltage protection on CH1

    psu.all_output_on()
    psu.check_errors()

    for name, m in psu.measure_all().items():
        print(name, m)               # CH1 12.00 V, 0.40 A, 4.8 W ...
# leaving the block turns all outputs OFF and returns to local control
```

### Connecting via VISA (USB / GPIB / LAN)

```python
psu = ITN6332B.open_visa("USB0::0x2EC7::...::INSTR")
psu = ITN6332B.open_visa("TCPIP0::192.168.1.50::inst0::INSTR")
psu = ITN6332B.open_usb()           # auto-discover first ITECH USB device
```

### Series / parallel tracking (CH1 + CH2)

```python
psu.set_tracking(psu.TRACK_SERIES)    # combine CH1+CH2 for up to ~60 V
psu.set_tracking(psu.TRACK_PARALLEL)  # combine for up to ~12 A
psu.set_tracking(psu.TRACK_OFF)       # independent (default)
```

## Web UI

A complete browser dashboard ships in `psu_control.web` — a dependency-free
server (Python stdlib only) that wraps the driver and serves a per-channel
control panel with live readouts and charts.

```bash
# Try it with no hardware — built-in simulator:
python -m psu_control.web --demo
# then open http://127.0.0.1:8080

# For a real instrument, start the server and connect from the UI:
python -m psu_control.web --port 8080 --host 0.0.0.0
```

The dashboard provides:

- **Connection panel** — host/port (raw TCP), VISA resource, or one-click **Demo**.
- **Master controls** — all-on / all-off and CH1+CH2 series/parallel tracking, `*RST`.
- **One card per channel** — live V/I/P meters, a real-time dual-trace
  (voltage + current) chart, a CV/CC badge, voltage & current-limit setpoints,
  OVP, and a large per-channel OUTPUT toggle. Setpoints are range-checked
  against each channel's ratings.

The JSON REST API (`/api/state`, `/api/measure`, `/api/connect`,
`/api/channel/<n>/setpoint`, `/api/channel/<n>/output`, `/api/channel/<n>/ovp`,
`/api/all_output`, `/api/tracking`, `/api/reset`) is documented in
`psu_control/web/server.py`.

> The server binds to `127.0.0.1` by default and has no authentication. Use
> `--host 0.0.0.0` to expose it on your LAN only on a trusted network.

## Command-line interface

```bash
python -m psu_control.cli --host 192.168.1.50 idn
python -m psu_control.cli --host 192.168.1.50 measure
python -m psu_control.cli --host 192.168.1.50 set --ch 1 --voltage 12 --current 2 --on
python -m psu_control.cli --host 192.168.1.50 on  --ch 2
python -m psu_control.cli --host 192.168.1.50 off --all
```

## API overview

| Area | Methods |
|------|---------|
| Identification | `idn`, `reset`, `clear_status`, `self_test`, `wait_complete` |
| Remote/local | `remote`, `local`, `beep` |
| Channels | `ch1`, `ch2`, `ch3`, `channel(n)`, `channels` |
| Per-channel | `set_voltage`, `get_voltage`, `set_current`, `get_current`, `set_voltage_limit`, `apply`, `output_on/off`, `output_enabled`, `set_ovp`, `measure_*`, `measure`, `regulation_mode` |
| Master | `all_output_on`, `all_output_off`, `measure_all`, `set_tracking` |
| State | `save_state`, `recall_state` |
| Errors | `next_error`, `check_errors` |
| Lifecycle | `shutdown`, `close`, context manager |

Need a command not wrapped here? The raw SCPI connection is always available:

```python
psu.ch1.select()                       # make CH1 active
ver = psu.scpi.query("SYSTem:VERSion?")
psu.scpi.write("OUTPut:STATe ON")
```

## Project layout

```
psu_control/
    __init__.py        # public exports
    it_n6332b.py       # ITN6332B driver + Channel + ChannelSpec
    scpi.py            # transport layer (PyVISA + raw TCP backends)
    exceptions.py      # exception hierarchy
    simulator.py       # in-process triple-channel SCPI simulator
    cli.py             # `python -m psu_control.cli`
    web/
        server.py      # stdlib HTTP backend + JSON API (`python -m psu_control.web`)
        static/        # index.html, style.css, app.js dashboard
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

- Command mnemonics follow the IT6300 series Programming Guide (which documents
  the IT6322/IT6332/IT6333 A/B/C models with the same channel architecture). If
  your firmware revision differs, use `psu.scpi.write/query` directly — the
  driver never hides the raw connection.
- The instrument is placed under remote control on connect. Call `psu.local()`
  (or exit the `with` block) to release the front panel.
- Always confirm wiring and limits before enabling an output.
