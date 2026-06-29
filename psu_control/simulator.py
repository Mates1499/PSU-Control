"""In-process SCPI simulator of an IT-N6332B (IT-M3100/IT-N6300 command set).

It speaks the raw-socket protocol on a background thread so the real
:class:`~psu_control.ScpiConnection` TCP backend can talk to it unchanged. It
models a single bidirectional output (addressable as channel 1) driving a
resistive load, with CV/CC priority, the protection suite, slew settings and
SCPI ``MIN`` / ``MAX`` range queries -- enough to exercise the driver, CLI and
web UI without any physical instrument.

It is *not* a precise electrical simulation. Use :class:`MockInstrument`
directly, or the ``SimulatedInstrument`` alias.
"""

from __future__ import annotations

import math
import random
import socket
import threading
import time

# Modelled instrument ratings (one bidirectional channel).
_V_MAX = 60.0
_V_MIN = 0.0
_I_MAX = 12.0    # sourcing
_I_MIN = -12.0   # sinking
_P_MAX = 360.0


class MockInstrument:
    """A tiny bidirectional SCPI responder. Context-manage to get ``(host, port)``."""

    IDN = "ITECH Ltd.,IT-N6332B,800001,1.05"

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.host, self.port = self._sock.getsockname()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._running = True

        self.noise = False
        self.load_ohms = 12.0     # resistive load when sourcing
        self.channel = 1
        self.state = {
            "output": False,
            "priority": "VOLT",
            "mode": "FIX",
            "voltage": 0.0,
            "current": _I_MAX,
            "power": _P_MAX,
            "ovp": _V_MAX,
            "ocp": _I_MAX,
            "opp": _P_MAX,
            "ovp_on": False,
            "ques": 0,
        }
        self.received: list[str] = []

    # -- lifecycle --------------------------------------------------------

    def start(self) -> "MockInstrument":
        if not self._thread.is_alive():
            self._thread.start()
        return self

    def __enter__(self) -> "MockInstrument":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._running = False
        try:
            self._sock.close()
        except OSError:
            pass

    # -- server loop ------------------------------------------------------

    def _serve(self) -> None:
        while self._running:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            buf = b""
            with conn:
                while self._running:
                    try:
                        chunk = conn.recv(4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, _, buf = buf.partition(b"\n")
                        cmd = line.decode("ascii").strip()
                        if not cmd:
                            continue
                        self.received.append(cmd)
                        reply = self._handle(cmd)
                        if reply is not None:
                            conn.sendall((reply + "\n").encode("ascii"))

    # -- command handling -------------------------------------------------

    def _handle(self, cmd: str) -> str | None:
        s = self.state
        up = cmd.upper()

        # --- common / system ---
        if up == "*IDN?":
            return self.IDN
        if up == "*RST":
            s.update(output=False, voltage=0.0, ques=0)
            self.channel = 1
            return None
        if up in ("*CLS", "SYSTEM:CLEAR"):
            s["ques"] = 0
            return None
        if up == "*OPC?":
            return "1"
        if up == "*TST?":
            return "0"
        if up == "SYSTEM:ERROR?":
            return '+0,"No error"'
        if up == "SYSTEM:VERSION?":
            return "1.0"
        if up.startswith("SYSTEM:") or up.startswith("*SAV") or up.startswith("*RCL"):
            return None
        if up in ("OUTPUT:PROTECTION:CLEAR", "OUTP:PROT:CLE"):
            s["ques"] = 0
            return None

        head, _, value = cmd.partition(" ")
        head_u = head.upper()
        # Queries attach "?" to the head (e.g. "VOLTage? MAX"); strip it so the
        # base mnemonic can be matched against the setter tables below.
        is_query = head_u.endswith("?")
        base_head = head_u[:-1] if is_query else head_u

        # --- channel selection ---
        if head_u in ("CHANNEL", "CHAN", "INSTRUMENT:SELECT", "INSTRUMENT", "INST:SEL", "INST") and value:
            try:
                self.channel = int(value)
            except ValueError:
                pass
            return None
        if up in ("CHANNEL?", "CHAN?", "INSTRUMENT:SELECT?", "INSTRUMENT?", "INST:SEL?", "INST?"):
            return str(self.channel)
        if head_u in ("CHANNEL:STATE?", "CHAN:STAT?"):
            return "1" if value.strip() in ("1", "") else "0"

        # --- priority / mode ---
        if head_u in ("SOURCE:FUNCTION:PRIORITY", "FUNCTION:PRIORITY", "FUNC:PRI") and value:
            s["priority"] = "CURR" if value.upper().startswith("CURR") else "VOLT"
            return None
        if up in ("SOURCE:FUNCTION:PRIORITY?", "FUNCTION:PRIORITY?", "FUNC:PRI?"):
            return "CURRent" if s["priority"] == "CURR" else "VOLTage"
        if head_u in ("SOURCE:FUNCTION:MODE", "FUNCTION:MODE", "FUNC:MODE") and value:
            s["mode"] = value.upper()[:3]
            return None

        # --- setpoint writes (with MIN/MAX-aware queries below) ---
        base_v = ("SOURCE:VOLTAGE:LEVEL:IMMEDIATE:AMPLITUDE", "VOLTAGE", "VOLT", "SOURCE:VOLTAGE")
        base_i = ("SOURCE:CURRENT:LEVEL:IMMEDIATE:AMPLITUDE", "CURRENT", "CURR", "SOURCE:CURRENT")
        base_p = ("SOURCE:POWER:LEVEL:IMMEDIATE:AMPLITUDE", "POWER", "POW", "SOURCE:POWER")
        if head_u in base_v and value and not value.endswith("?"):
            s["voltage"] = self._clamp(value, _V_MIN, _V_MAX, s["voltage"])
            return None
        if head_u in base_i and value and not value.endswith("?"):
            s["current"] = self._clamp(value, _I_MIN, _I_MAX, s["current"])
            return None
        if head_u in base_p and value and not value.endswith("?"):
            s["power"] = self._clamp(value, 0.0, _P_MAX, s["power"])
            return None

        setters = {
            "SOURCE:VOLTAGE:PROTECTION:LEVEL": "ovp",
            "SOURCE:CURRENT:OVER:PROTECTION:LEVEL": "ocp",
            "SOURCE:POWER:PROTECTION:LEVEL": "opp",
        }
        if head_u in setters and value:
            s[setters[head_u]] = float(value)
            return None
        if head_u == "SOURCE:VOLTAGE:PROTECTION:STATE" and value:
            s["ovp_on"] = value.upper() in ("ON", "1")
            return None
        # accept (ignore) the various slew / limit / under-protection writes
        if any(k in head_u for k in (":SLEW", ":LIMIT", ":UNDER:", ":PROTECTION:", ":DELAY")) and value:
            return None

        if head_u in ("SOURCE:APPLY", "APPLY") and value:
            parts = [p for p in value.replace(",", " ").split() if p]
            if parts:
                s["voltage"] = self._clamp(parts[0], _V_MIN, _V_MAX, s["voltage"])
            if len(parts) > 1:
                s["current"] = self._clamp(parts[1], _I_MIN, _I_MAX, s["current"])
            return None
        if head_u in ("OUTPUT:STATE", "OUTPUT", "OUTP", "OUTP:STAT") and value:
            s["output"] = value.upper() in ("ON", "1")
            return None

        # --- queries (incl. MIN/MAX ranges) ---
        if is_query and base_head in base_v:
            return self._range_reply(value, s["voltage"], _V_MIN, _V_MAX)
        if is_query and base_head in base_i:
            return self._range_reply(value, s["current"], _I_MIN, _I_MAX)
        if is_query and base_head in base_p:
            return self._range_reply(value, s["power"], 0.0, _P_MAX)
        if up in ("OUTPUT:STATE?", "OUTPUT?", "OUTP?", "OUTP:STAT?"):
            return "1" if s["output"] else "0"
        if up == "STATUS:QUESTIONABLE:CONDITION?":
            return str(s["ques"])
        if up in ("MEASURE:SCALAR:VOLTAGE:DC?", "MEAS:VOLT:DC?", "MEASURE:VOLTAGE?"):
            return f"{self._measure()[0]:.4f}"
        if up in ("MEASURE:SCALAR:CURRENT:DC?", "MEAS:CURR:DC?", "MEASURE:CURRENT?"):
            return f"{self._measure()[1]:.4f}"
        if up in ("MEASURE:SCALAR:POWER:DC?", "MEAS:POW:DC?", "MEASURE:POWER?"):
            v, i = self._measure()
            return f"{v * i:.4f}"
        if up.startswith("FETCH"):
            v, i = self._measure()
            if "CURR" in up:
                return f"{i:.4f}"
            if "POW" in up:
                return f"{v * i:.4f}"
            return f"{v:.4f}"

        if cmd.endswith("?"):
            return "0"
        return None

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _clamp(value: str, lo: float, hi: float, prev: float) -> float:
        v = value.strip().upper()
        if v in ("MAX", "MAXIMUM"):
            return hi
        if v in ("MIN", "MINIMUM"):
            return lo
        try:
            return max(lo, min(hi, float(value)))
        except ValueError:
            return prev

    @staticmethod
    def _range_reply(arg: str, current: float, lo: float, hi: float) -> str:
        a = arg.strip().upper()
        if a in ("MAX", "MAXIMUM"):
            return f"{hi:.4f}"
        if a in ("MIN", "MINIMUM"):
            return f"{lo:.4f}"
        return f"{current:.4f}"

    def _measure(self) -> tuple[float, float]:
        """Model the output into the load, honouring CC limiting."""
        s = self.state
        if not s["output"]:
            return 0.0, 0.0
        v = s["voltage"]
        i = v / self.load_ohms
        if i > s["current"] > 0:  # constant-current limiting
            i = s["current"]
            v = i * self.load_ohms
        if self.noise:
            t = time.monotonic()
            v += 0.01 * math.sin(t * 3.0) + random.uniform(-0.005, 0.005)
            i += 0.005 * math.sin(t * 5.0) + random.uniform(-0.003, 0.003)
        return v, i


# Public, descriptive alias for the simulator.
SimulatedInstrument = MockInstrument
