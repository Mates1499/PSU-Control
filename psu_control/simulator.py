"""In-process SCPI simulator of an IT-N6332B triple-channel supply.

It speaks the raw-socket protocol on a background thread so the real
:class:`~psu_control.ScpiConnection` TCP backend can talk to it unchanged. It
models three channels with the ITECH ``INSTrument`` channel-selection handshake,
each driving a resistive load (with optional measurement noise) so the web UI
and tests work without any physical instrument.

It is *not* a precise electrical simulation. Use :class:`MockInstrument`
directly, or the ``SimulatedInstrument`` alias.
"""

from __future__ import annotations

import math
import random
import socket
import threading
import time

# Per-channel factory ratings (mirrors IT_N6332B_CHANNELS in the driver).
_CHANNEL_SPECS = {
    1: {"vmax": 30.0, "imax": 6.0, "load": 30.0},
    2: {"vmax": 30.0, "imax": 6.0, "load": 30.0},
    3: {"vmax": 5.0, "imax": 3.0, "load": 5.0},
}


class MockInstrument:
    """A tiny three-channel SCPI responder. Context-manage to get ``(host, port)``."""

    IDN = "ITECH Ltd.,IT-N6332B,800001,1.05"

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.host, self.port = self._sock.getsockname()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._running = True

        # Whether to add small ripple/noise to measurements.
        self.noise = False

        # Currently selected channel (INSTrument:NSELect) and per-channel state.
        self.selected = 1
        self.channels = {
            n: {
                "output": False,
                "voltage": 0.0,
                "current": spec["imax"],  # current limit defaults to max
                "vlimit": spec["vmax"],
                "ovp": spec["vmax"],
                "ovp_on": False,
                "load": spec["load"],
                "vmax": spec["vmax"],
                "imax": spec["imax"],
            }
            for n, spec in _CHANNEL_SPECS.items()
        }
        self.track = "OFF"
        self.received: list[str] = []

    # -- lifecycle --------------------------------------------------------

    def start(self) -> "MockInstrument":
        """Start the background server thread and return self."""
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
        # Accept sequential client connections, like a reconnectable instrument.
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
        up = cmd.upper()
        ch = self.channels[self.selected]

        # --- common / system ---
        if up == "*IDN?":
            return self.IDN
        if up == "*RST":
            for c in self.channels.values():
                c.update(output=False, voltage=0.0)
            self.selected = 1
            return None
        if up == "*CLS":
            return None
        if up == "*OPC?":
            return "1"
        if up == "*TST?":
            return "0"
        if up == "SYSTEM:ERROR?":
            return '+0,"No error"'
        if up.startswith("SYSTEM:"):
            return None
        if up.startswith("*SAV") or up.startswith("*RCL"):
            return None

        head, _, value = cmd.partition(" ")
        head_u = head.upper()

        # --- channel selection ---
        if head_u in ("INSTRUMENT:NSELECT", "INST:NSEL"):
            try:
                self.selected = int(value)
            except ValueError:
                pass
            return None
        if head_u in ("INSTRUMENT:SELECT", "INSTRUMENT", "INST:SEL", "INST"):
            self.selected = {"CH1": 1, "CH2": 2, "CH3": 3}.get(value.upper().strip(), self.selected)
            return None
        if up in ("INSTRUMENT:NSELECT?", "INST:NSEL?"):
            return str(self.selected)
        if up in ("INSTRUMENT:SELECT?", "INSTRUMENT?", "INST:SEL?", "INST?"):
            return f"CH{self.selected}"

        # --- setpoint writes ---
        if head_u in ("VOLTAGE", "VOLT", "SOURCE:VOLTAGE") and value:
            ch["voltage"] = min(float(value), ch["vmax"])
            return None
        if head_u in ("CURRENT", "CURR", "SOURCE:CURRENT") and value:
            ch["current"] = min(float(value), ch["imax"])
            return None
        if head_u in ("VOLTAGE:LIMIT", "VOLT:LIM") and value:
            ch["vlimit"] = float(value)
            return None
        if head_u in ("VOLTAGE:PROTECTION:LEVEL", "VOLT:PROT:LEV") and value:
            ch["ovp"] = float(value)
            return None
        if head_u in ("VOLTAGE:PROTECTION:STATE", "VOLT:PROT:STAT") and value:
            ch["ovp_on"] = value.upper() in ("ON", "1")
            return None
        if head_u == "APPLY" and value:
            parts = [p for p in value.replace(",", " ").split() if p]
            if parts:
                ch["voltage"] = min(float(parts[0]), ch["vmax"])
            if len(parts) > 1:
                ch["current"] = min(float(parts[1]), ch["imax"])
            return None
        if head_u in ("OUTPUT:STATE", "OUTPUT", "OUTP", "OUTP:STAT") and value:
            ch["output"] = value.upper() in ("ON", "1")
            return None
        if head_u in ("OUTPUT:TRACK", "OUTP:TRAC") and value:
            self.track = value.upper()
            return None

        # --- queries ---
        if up in ("VOLTAGE?", "VOLT?", "SOURCE:VOLTAGE?"):
            return f"{ch['voltage']:.4f}"
        if up in ("CURRENT?", "CURR?", "SOURCE:CURRENT?"):
            return f"{ch['current']:.4f}"
        if up in ("VOLTAGE:LIMIT?", "VOLT:LIM?"):
            return f"{ch['vlimit']:.4f}"
        if up in ("OUTPUT:STATE?", "OUTPUT?", "OUTP?", "OUTP:STAT?"):
            return "1" if ch["output"] else "0"
        if up == "APPLY?":
            return f"{ch['voltage']:.4f},{ch['current']:.4f}"
        if up in ("MEASURE:VOLTAGE:DC?", "MEAS:VOLT:DC?", "MEASURE:VOLTAGE?", "MEAS:VOLT?"):
            return f"{self._measure(ch)[0]:.4f}"
        if up in ("MEASURE:CURRENT:DC?", "MEAS:CURR:DC?", "MEASURE:CURRENT?", "MEAS:CURR?"):
            return f"{self._measure(ch)[1]:.4f}"
        if up in ("MEASURE:POWER:DC?", "MEAS:POW:DC?", "MEASURE:POWER?", "MEAS:POW?"):
            v, i = self._measure(ch)
            return f"{v * i:.4f}"

        # ignore unknown writes; stub unknown queries
        if cmd.endswith("?"):
            return "0"
        return None

    def _measure(self, ch) -> tuple[float, float]:
        """Model one channel into its load resistor, honouring the CC limit."""
        if not ch["output"]:
            return 0.0, 0.0
        v = ch["voltage"]
        i = v / ch["load"]
        if i > ch["current"] > 0:  # constant-current limiting
            i = ch["current"]
            v = i * ch["load"]
        if self.noise:
            t = time.monotonic()
            v += 0.005 * math.sin(t * 3.0) + random.uniform(-0.003, 0.003)
            i += 0.003 * math.sin(t * 5.0) + random.uniform(-0.002, 0.002)
        return max(0.0, v), max(0.0, i)


# Public, descriptive alias for the simulator.
SimulatedInstrument = MockInstrument
