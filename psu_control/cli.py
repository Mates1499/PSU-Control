"""Command-line interface for quick control of an IT-N6332B.

Examples::

    python -m psu_control.cli --host 192.168.1.50 idn
    python -m psu_control.cli --host 192.168.1.50 measure
    python -m psu_control.cli --host 192.168.1.50 set --ch 1 --voltage 12 --current 2 --on
    python -m psu_control.cli --host 192.168.1.50 on --ch 2
    python -m psu_control.cli --host 192.168.1.50 off --all
"""

from __future__ import annotations

import argparse
import sys

from . import ITN6332B, PSUError
from .scpi import DEFAULT_SCPI_PORT


def _connect(args: argparse.Namespace) -> ITN6332B:
    if args.visa:
        return ITN6332B.open_visa(args.visa, timeout=args.timeout)
    return ITN6332B.open_tcp(args.host, args.port, timeout=args.timeout)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="psu_control.cli",
        description="Control an ITECH IT-N6332B triple-channel power supply over SCPI.",
    )
    p.add_argument("--host", default="192.168.1.50", help="Instrument IP/hostname")
    p.add_argument("--port", type=int, default=DEFAULT_SCPI_PORT, help="Raw SCPI port")
    p.add_argument("--visa", help="Use a VISA resource string instead of TCP")
    p.add_argument("--timeout", type=float, default=5.0, help="I/O timeout (s)")

    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("idn", help="Print *IDN? identification")
    sub.add_parser("reset", help="Send *RST")
    sub.add_parser("measure", help="Print measured V/I/P for all channels")

    s = sub.add_parser("set", help="Set a channel's voltage/current and optionally enable")
    s.add_argument("--ch", type=int, required=True, choices=(1, 2, 3))
    s.add_argument("--voltage", type=float, help="Voltage setpoint (V)")
    s.add_argument("--current", type=float, help="Current limit (A)")
    s.add_argument("--ovp", type=float, help="Over-voltage protection level (V)")
    s.add_argument("--on", action="store_true", help="Enable the channel afterwards")

    on = sub.add_parser("on", help="Enable output")
    on.add_argument("--ch", type=int, choices=(1, 2, 3))
    on.add_argument("--all", action="store_true")

    off = sub.add_parser("off", help="Disable output")
    off.add_argument("--ch", type=int, choices=(1, 2, 3))
    off.add_argument("--all", action="store_true")
    return p


def run(args: argparse.Namespace) -> int:
    # Manage the connection manually so output state set by `on`/`set --on`
    # survives (the context manager would fail-safe everything off on exit).
    psu = _connect(args)
    try:
        cmd = args.command
        if cmd == "idn":
            print(psu.idn())
        elif cmd == "reset":
            psu.reset()
            print("Reset complete.")
        elif cmd == "measure":
            for name, m in psu.measure_all().items():
                print(f"{name}: {m}")
        elif cmd == "set":
            ch = psu.channel(args.ch)
            if args.voltage is not None and args.current is not None:
                ch.apply(args.voltage, args.current)
            else:
                if args.voltage is not None:
                    ch.set_voltage(args.voltage)
                if args.current is not None:
                    ch.set_current(args.current)
            if args.ovp is not None:
                ch.set_ovp(args.ovp)
            if args.on:
                ch.output_on()
            psu.check_errors()
            print(f"CH{args.ch}: {ch.measure()}")
        elif cmd in ("on", "off"):
            enable = cmd == "on"
            if args.all or args.ch is None:
                psu.all_output_on() if enable else psu.all_output_off()
                print(f"All outputs {'ON' if enable else 'OFF'}")
            else:
                psu.channel(args.ch).set_output(enable)
                print(f"CH{args.ch} {'ON' if enable else 'OFF'}")
    finally:
        psu.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (PSUError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
