"""A minimal in-process SCPI mock of an IT-N6332B, for tests and dry runs.

It speaks the raw-socket protocol on a background thread so the real
:class:`~psu_control.ScpiConnection` TCP backend can talk to it unchanged.
It models just enough state (setpoints, output flag, measurements) to verify
command formatting and round-trips -- it is *not* an electrical simulation.
"""

from __future__ import annotations

import socket
import threading


class MockInstrument:
    """A tiny SCPI responder. Use as a context manager to get ``(host, port)``."""

    IDN = "ITECH Ltd.,IT-N6332B,800001,1.05"

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.host, self.port = self._sock.getsockname()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._running = True

        # Modelled instrument state.
        self.state = {
            "output": False,
            "func": "VOLT",
            "voltage": 0.0,
            "current": 0.0,
            "ilim_pos": 5.0,
            "ilim_neg": -5.0,
            "ovp": 0.0,
            "ocp": 0.0,
            "opp": 0.0,
            "ques": 0,
        }
        self.received: list[str] = []

    # -- lifecycle --------------------------------------------------------

    def __enter__(self) -> "MockInstrument":
        self._thread.start()
        return self

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
        # Accept sequential client connections (one at a time), like a real
        # instrument that can be reconnected to between sessions.
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

        if up == "*IDN?":
            return self.IDN
        if up in ("*RST",):
            self.state.update(output=False, voltage=0.0, current=0.0, ques=0)
            return None
        if up in ("*CLS",):
            s["ques"] = 0
            return None
        if up == "*OPC?":
            return "1"
        if up == "*TST?":
            return "0"
        if up == "SYSTEM:ERROR?":
            return '+0,"No error"'
        if up.startswith("SYSTEM:") or up.startswith("OUTPUT:PROTECTION:CLEAR"):
            if up == "OUTPUT:PROTECTION:CLEAR":
                s["ques"] = 0
            return None

        # value-bearing writes "<head> <value>"
        head, _, value = cmd.partition(" ")
        head_u = head.upper()

        setters = {
            "SOURCE:VOLTAGE:LEVEL:IMMEDIATE:AMPLITUDE": ("voltage", float),
            "SOURCE:CURRENT:LEVEL:IMMEDIATE:AMPLITUDE": ("current", float),
            "SOURCE:CURRENT:LIMIT:POSITIVE": ("ilim_pos", float),
            "SOURCE:CURRENT:LIMIT:NEGATIVE": ("ilim_neg", float),
            "SOURCE:VOLTAGE:PROTECTION:LEVEL": ("ovp", float),
            "SOURCE:CURRENT:PROTECTION:LEVEL": ("ocp", float),
            "SOURCE:POWER:PROTECTION:LEVEL": ("opp", float),
        }
        if head_u in setters and value:
            key, conv = setters[head_u]
            s[key] = conv(value)
            return None

        if head_u == "OUTPUT:STATE":
            s["output"] = value.upper() in ("ON", "1")
            return None
        if head_u == "SOURCE:FUNCTION":
            s["func"] = "CURR" if value.upper().startswith("CURR") else "VOLT"
            return None

        # queries
        if up == "OUTPUT:STATE?":
            return "1" if s["output"] else "0"
        if up == "SOURCE:FUNCTION?":
            return s["func"]
        if up == "SOURCE:VOLTAGE:LEVEL:IMMEDIATE:AMPLITUDE?":
            return f"{s['voltage']:.6f}"
        if up == "SOURCE:CURRENT:LEVEL:IMMEDIATE:AMPLITUDE?":
            return f"{s['current']:.6f}"
        if up == "SOURCE:CURRENT:LIMIT:POSITIVE?":
            return f"{s['ilim_pos']:.6f}"
        if up == "SOURCE:CURRENT:LIMIT:NEGATIVE?":
            return f"{s['ilim_neg']:.6f}"
        if up == "STATUS:QUESTIONABLE:CONDITION?":
            return str(s["ques"])
        if up == "MEASURE:SCALAR:VOLTAGE:DC?":
            return f"{s['voltage'] if s['output'] else 0.0:.6f}"
        if up == "MEASURE:SCALAR:CURRENT:DC?":
            # Pretend a 6 ohm load is attached when sourcing.
            i = (s["voltage"] / 6.0) if s["output"] else 0.0
            return f"{i:.6f}"
        if up == "MEASURE:SCALAR:POWER:DC?":
            v = s["voltage"] if s["output"] else 0.0
            i = (v / 6.0) if s["output"] else 0.0
            return f"{v * i:.6f}"

        # ignore unknown writes; respond to unknown queries with a stub
        if cmd.endswith("?"):
            return "0"
        return None
