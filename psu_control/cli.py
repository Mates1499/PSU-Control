"""Command-line interface for quick control of an IT-N6332B.

Examples::

    python -m psu_control.cli --host 192.168.1.50 idn
    python -m psu_control.cli --host 192.168.1.50 measure
    python -m psu_control.cli --host 192.168.1.50 set --voltage 12 --current 2 --priority cv --on
    python -m psu_control.cli --host 192.168.1.50 off
"""

from __future__ import annotations

import argparse
import sys

from . import ITN6332B, Priority, PSUError
from .scpi import DEFAULT_SCPI_PORT

_PRIORITY = {"cv": Priority.VOLTAGE, "cc": Priority.CURRENT,
             "voltage": Priority.VOLTAGE, "current": Priority.CURRENT}


def _connect(args: argparse.Namespace) -> ITN6332B:
    if args.visa:
        return ITN6332B.open_visa(args.visa, channel=args.channel, timeout=args.timeout)
    return ITN6332B.open_tcp(args.host, args.port, channel=args.channel, timeout=args.timeout)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="psu_control.cli",
        description="Control an ITECH IT-N6332B bidirectional power supply over SCPI.",
    )
    p.add_argument("--host", default="192.168.1.50", help="Instrument IP/hostname")
    p.add_argument("--port", type=int, default=DEFAULT_SCPI_PORT, help="Raw SCPI port (default 30000)")
    p.add_argument("--visa", help="Use a VISA resource string instead of TCP")
    p.add_argument("--channel", type=int, default=1, help="Channel number (1-16, multi-unit systems)")
    p.add_argument("--timeout", type=float, default=5.0, help="I/O timeout (s)")

    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("idn", help="Print *IDN? identification")
    sub.add_parser("reset", help="Send *RST")
    sub.add_parser("measure", help="Print measured V/I/P")
    sub.add_parser("status", help="Print protection status")
    sub.add_parser("on", help="Enable output")
    sub.add_parser("off", help="Disable output")
    sub.add_parser("clear", help="Clear latched protection")

    s = sub.add_parser("set", help="Set voltage/current/priority and optionally enable")
    s.add_argument("--voltage", type=float, help="Voltage setpoint (V)")
    s.add_argument("--current", type=float, help="Current setpoint/limit (A)")
    s.add_argument("--priority", choices=sorted(_PRIORITY), help="Regulation priority")
    s.add_argument("--ovp", type=float, help="Over-voltage protection level (V)")
    s.add_argument("--ocp", type=float, help="Over-current protection level (A)")
    s.add_argument("--on", action="store_true", help="Enable output afterwards")
    return p


def run(args: argparse.Namespace) -> int:
    # Manage the connection manually so output state set by `on`/`set --on`
    # survives (the context manager fails everything off on exit).
    psu = _connect(args)
    try:
        cmd = args.command
        if cmd == "idn":
            print(psu.idn())
        elif cmd == "reset":
            psu.reset(); print("Reset complete.")
        elif cmd == "measure":
            print(psu.measure())
        elif cmd == "status":
            print("TRIPPED" if psu.protection_tripped() else "OK (no protection tripped)")
        elif cmd == "on":
            psu.output_on(); print("Output ON")
        elif cmd == "off":
            psu.output_off(); print("Output OFF")
        elif cmd == "clear":
            psu.clear_protection(); print("Protection cleared.")
        elif cmd == "set":
            if args.priority:
                psu.set_priority(_PRIORITY[args.priority])
            if args.voltage is not None and args.current is not None:
                psu.apply(args.voltage, args.current)
            else:
                if args.voltage is not None:
                    psu.set_voltage(args.voltage)
                if args.current is not None:
                    psu.set_current(args.current)
            if args.ovp is not None:
                psu.set_ovp(args.ovp)
            if args.ocp is not None:
                psu.set_ocp(args.ocp)
            if args.on:
                psu.output_on()
            psu.check_errors()
            print("Applied:", psu.measure())
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
