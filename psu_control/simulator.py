"""In-process SCPI simulators for the supported PSU models.

All simulators speak the raw-socket protocol on a background thread so the real
:class:`~psu_control.ScpiConnection` TCP backend can talk to them unchanged.
They model the minimum command set needed to exercise the driver, CLI and web UI
without any physical instrument.

Classes
-------
MockInstrument / SimulatedInstrument
    Simulates the ITECH IT-N6332B (IT-M3100/IT-N6300 SCPI command set).
    Supports multiple bidirectional channels, CV/CC priority, protections,
    slew settings and SCPI MIN/MAX range queries.

    ``MockInstrument()``           -- 3 channels by default
    ``MockInstrument(channels=1)`` -- single-output unit

CPX200DPSimulator
    Simulates the Aim-TTi CPX200DP (suffix-addressed ASCII command set).
    Always has 2 source-only channels.
"""

from __future__ import annotations

import math
import random
import re
import socket
import threading
import time

# Per-channel ratings of the simulated unit.
_V_MAX = 60.0
_V_MIN = 0.0
_I_MAX = 12.0    # sourcing
_I_MIN = -12.0   # sinking
_P_MAX = 360.0


def _new_channel_state() -> dict:
    return {
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
        "load": 12.0,   # resistive load when sourcing
    }


class MockInstrument:
    """A tiny multi-channel bidirectional SCPI responder."""

    IDN = "ITECH Ltd.,IT-N6332B,800001,1.05"

    def __init__(self, channels: int = 3, dialect: str = "extended") -> None:
        """``dialect`` mirrors the instrument's *System → Instructions* mode:

        * ``"extended"`` (default) -- IT6300-compatible; channel select is
          ``INST:NSEL <n>`` and the Standard forms raise error 150.
        * ``"standard"`` -- IT-N6300 native; ``CHAN <n>`` / ``INST <n>`` work
          and ``INST:NSEL`` raises error 150.
        * ``"any"`` -- accept every form (lenient test double).
        """
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.host, self.port = self._sock.getsockname()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._running = True

        self.noise = False
        self.dialect = dialect
        self.num_channels = max(1, channels)
        self.selected = 1
        self.channels = {n: _new_channel_state() for n in range(1, self.num_channels + 1)}
        self.received: list[str] = []
        self.errors: list[tuple[int, str]] = []

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

    def _cur(self) -> dict:
        return self.channels.get(self.selected, self.channels[1])

    def _handle(self, cmd: str) -> str | None:
        up = cmd.upper()

        # --- common / system (channel-independent) ---
        if up == "*IDN?":
            return self.IDN
        if up == "*RST":
            for st in self.channels.values():
                st.update(output=False, voltage=0.0, ques=0)
            self.selected = 1
            return None
        if up in ("*CLS", "SYSTEM:CLEAR"):
            for st in self.channels.values():
                st["ques"] = 0
            self.errors.clear()
            return None
        if up == "*OPC?":
            return "1"
        if up == "*TST?":
            return "0"
        if up in ("SYSTEM:ERROR?", "SYST:ERR?"):
            if self.errors:
                code, msg = self.errors.pop(0)
                return f'{code},"{msg}"'
            return '+0,"No error"'
        if up == "SYSTEM:VERSION?":
            return "1.0"
        if up.startswith("SYSTEM:") or up.startswith("*SAV") or up.startswith("*RCL"):
            return None
        if up in ("OUTPUT:PROTECTION:CLEAR", "OUTP:PROT:CLE"):
            self._cur()["ques"] = 0
            return None

        head, _, value = cmd.partition(" ")
        head_u = head.upper()
        is_query = head_u.endswith("?")
        base_head = head_u[:-1] if is_query else head_u

        # --- channel selection / availability ---
        if head_u in ("CHANNEL", "CHAN", "INSTRUMENT:SELECT", "INSTRUMENT", "INST:SEL", "INST",
                      "INST:NSEL") and value:
            is_extended_form = head_u in ("INST:NSEL", "INSTRUMENT:NSELECT")
            if (self.dialect == "extended" and not is_extended_form) or \
               (self.dialect == "standard" and is_extended_form):
                self.errors.append((150, "Wrong parameter"))
                return None
            try:
                self.selected = int(value)
            except ValueError:
                pass
            return None
        if up in ("CHANNEL?", "CHAN?", "INSTRUMENT:SELECT?", "INSTRUMENT?", "INST:SEL?", "INST?",
                  "INST:NSEL?"):
            return str(self.selected)
        if base_head in ("CHANNEL:STATE", "CHAN:STAT") and is_query:
            try:
                n = int(value)
            except ValueError:
                n = self.selected
            return "1" if 1 <= n <= self.num_channels else "0"

        s = self._cur()

        # --- priority / mode ---
        if head_u in ("SOURCE:FUNCTION:PRIORITY", "FUNCTION:PRIORITY", "FUNC:PRI") and value:
            s["priority"] = "CURR" if value.upper().startswith("CURR") else "VOLT"
            return None
        if up in ("SOURCE:FUNCTION:PRIORITY?", "FUNCTION:PRIORITY?", "FUNC:PRI?"):
            return "CURRent" if s["priority"] == "CURR" else "VOLTage"
        if head_u in ("SOURCE:FUNCTION:MODE", "FUNCTION:MODE", "FUNC:MODE") and value:
            s["mode"] = value.upper()[:3]
            return None

        base_v = ("SOURCE:VOLTAGE:LEVEL:IMMEDIATE:AMPLITUDE", "VOLTAGE", "VOLT", "SOURCE:VOLTAGE")
        base_i = ("SOURCE:CURRENT:LEVEL:IMMEDIATE:AMPLITUDE", "CURRENT", "CURR", "SOURCE:CURRENT")
        base_p = ("SOURCE:POWER:LEVEL:IMMEDIATE:AMPLITUDE", "POWER", "POW", "SOURCE:POWER")

        # --- setpoint writes ---
        if head_u in base_v and value and not is_query:
            s["voltage"] = self._clamp(value, _V_MIN, _V_MAX, s["voltage"])
            return None
        if head_u in base_i and value and not is_query:
            s["current"] = self._clamp(value, _I_MIN, _I_MAX, s["current"])
            return None
        if head_u in base_p and value and not is_query:
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
        if any(k in head_u for k in (":SLEW", ":LIMIT", ":UNDER:", ":PROTECTION:", ":DELAY")) and value:
            return None  # accept-and-ignore slew/limit/under-protection writes

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
        if up in ("MEASURE:SCALAR:VOLTAGE:DC?", "MEAS:VOLT:DC?", "MEASURE:VOLTAGE?", "MEAS:VOLT?"):
            return f"{self._measure(s)[0]:.4f}"
        if up in ("MEASURE:SCALAR:CURRENT:DC?", "MEAS:CURR:DC?", "MEASURE:CURRENT?", "MEAS:CURR?"):
            return f"{self._measure(s)[1]:.4f}"
        if up in ("MEASURE:SCALAR:POWER:DC?", "MEAS:POW:DC?", "MEASURE:POWER?", "MEAS:POW?"):
            v, i = self._measure(s)
            return f"{v * i:.4f}"
        if up.startswith("FETCH"):
            v, i = self._measure(s)
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

    def _measure(self, s: dict) -> tuple[float, float]:
        """Model a channel's output into its load, honouring CC limiting."""
        if not s["output"]:
            return 0.0, 0.0
        v = s["voltage"]
        i = v / s["load"]
        if i > s["current"] > 0:  # constant-current limiting
            i = s["current"]
            v = i * s["load"]
        if self.noise:
            t = time.monotonic()
            v += 0.01 * math.sin(t * 3.0) + random.uniform(-0.005, 0.005)
            i += 0.005 * math.sin(t * 5.0) + random.uniform(-0.003, 0.003)
        return v, i


# Public, descriptive alias for the IT-N6332B simulator.
SimulatedInstrument = MockInstrument


# -------------------------------------------------------------------------- #
# Aim-TTi CPX200DP simulator
# -------------------------------------------------------------------------- #

_CPX_V_MAX = 60.0
_CPX_I_MAX = 10.0

# Real Aim-TTi command grammar: a letter mnemonic, a channel digit, and an
# optional trailing "O" (readback) — e.g. V1, I2, OP1, OVP2, LSR1, V1O.
_CMD_RE = re.compile(r"^([A-Z]+?)(\d+)(O?)$")


def _new_cpx_channel() -> dict:
    return {
        "output": False,
        "voltage": 0.0,
        "current": _CPX_I_MAX,
        "ovp": _CPX_V_MAX + 1.0,
        "ocp": _CPX_I_MAX + 0.5,
        "tripped": False,
        "load": 12.0,
    }


class CPX200DPSimulator:
    """Tiny TCP responder simulating the Aim-TTi CPX200DP ASCII command set.

    Speaks the documented remote commands: ``V<n>``/``I<n>`` setpoints
    (queries reply prefixed, e.g. ``"V1 12.00"``), ``V<n>O?``/``I<n>O?``
    readbacks with unit suffix (``"12.000V"``), ``OP<n>``, ``OPALL``,
    ``OVP<n>``/``OCP<n>``, ``TRIPRST`` and read-and-clear ``LSR<n>?``.
    """

    IDN = "THURLBY THANDAR INSTRUMENTS,CPX200DP,000000,1.00"

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.host, self.port = self._sock.getsockname()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._running = True
        self.channels: dict[int, dict] = {1: _new_cpx_channel(), 2: _new_cpx_channel()}
        self.received: list[str] = []

    def start(self) -> "CPX200DPSimulator":
        if not self._thread.is_alive():
            self._thread.start()
        return self

    def __enter__(self) -> "CPX200DPSimulator":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._running = False
        try:
            self._sock.close()
        except OSError:
            pass

    # -- server loop -------------------------------------------------------

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
                        raw = line.decode("ascii").strip()
                        if not raw:
                            continue
                        self.received.append(raw)
                        reply = self._handle(raw)
                        if reply is not None:
                            conn.sendall((reply + "\n").encode("ascii"))

    # -- command dispatch --------------------------------------------------

    def _handle(self, raw: str) -> str | None:
        parts = raw.split(None, 1)
        token = parts[0].upper()
        value = parts[1].strip() if len(parts) > 1 else ""
        is_query = token.endswith("?")
        base_token = token.rstrip("?")

        # --- channel-independent commands ---
        if base_token == "*IDN":
            return self.IDN
        if base_token == "*RST":
            for ch in self.channels.values():
                ch.update(output=False, voltage=0.0, tripped=False)
            return None
        if base_token in ("*CLS", "*OPC"):
            return "1" if is_query else None
        if base_token == "*TST":
            return "0"
        if base_token == "OPALL":
            on = value.strip() == "1"
            for ch in self.channels.values():
                ch["output"] = on
            return None
        if base_token in ("LOCAL", "IFLOCK", "IFUNLOCK", "TRIPRST"):
            if base_token == "TRIPRST":
                for ch in self.channels.values():
                    ch["tripped"] = False
            if base_token in ("IFLOCK", "IFUNLOCK"):
                return "1" if base_token == "IFLOCK" else "0"
            return None

        # --- suffix-addressed commands: CMD<n>[O][?] ---
        m = _CMD_RE.match(base_token)
        if not m:
            return "0" if is_query else None
        cmd_name, n, readback = m.group(1), int(m.group(2)), m.group(3)
        if n not in self.channels:
            return "0" if is_query else None
        ch = self.channels[n]

        if cmd_name == "V":
            if readback and is_query:      # V<n>O?  -> "12.000V"
                v, _ = self._measure(ch)
                return f"{v:.3f}V"
            if is_query:                   # V<n>?   -> "V<n> 12.00"
                return f"V{n} {ch['voltage']:.2f}"
            try:
                ch["voltage"] = max(0.0, min(_CPX_V_MAX, float(value)))
            except ValueError:
                pass
            return None

        if cmd_name == "I":
            if readback and is_query:      # I<n>O?  -> "1.000A"
                _, i = self._measure(ch)
                return f"{i:.3f}A"
            if is_query:                   # I<n>?   -> "I<n> 1.000"
                return f"I{n} {ch['current']:.3f}"
            try:
                ch["current"] = max(0.0, min(_CPX_I_MAX, float(value)))
            except ValueError:
                pass
            return None

        if cmd_name == "OP":
            if is_query:
                return "1" if ch["output"] else "0"
            ch["output"] = value.strip() == "1"
            return None

        if cmd_name == "OVP":
            if is_query:                   # -> "VP<n> 13.50"
                return f"VP{n} {ch['ovp']:.2f}"
            try:
                ch["ovp"] = float(value)
            except ValueError:
                pass
            return None

        if cmd_name == "OCP":
            if is_query:                   # -> "CP<n> 2.500"
                return f"CP{n} {ch['ocp']:.3f}"
            try:
                ch["ocp"] = float(value)
            except ValueError:
                pass
            return None

        if cmd_name == "LSR":
            # Read-and-clear Limit Event Status Register.
            # bit0 = CV, bit1 = CC, bit2 = OVP trip (used for any sim trip).
            val = 0
            if ch["output"]:
                v = ch["voltage"]
                cc = ch["load"] > 0 and v / ch["load"] > ch["current"] > 0
                val |= 0x02 if cc else 0x01
            if ch["tripped"]:
                val |= 0x04
            return str(val)

        return "0" if is_query else None

    # -- electrical model --------------------------------------------------

    def _measure(self, ch: dict) -> tuple[float, float]:
        """Model a CV supply with CC limiting into a resistive load."""
        if not ch["output"]:
            return 0.0, 0.0
        v = ch["voltage"]
        i = v / ch["load"] if ch["load"] > 0 else 0.0
        if i > ch["current"] > 0:
            i = ch["current"]
            v = i * ch["load"]
        return v, i
