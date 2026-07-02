"""Driver for the ITECH IT-N6332B / IT-N6300-series DC power supply.

Uses the IT-N6300 short-form SCPI command set.  The IT-N6332B has **three
independent channels**.

Command forms used::

    INST:NSEL 1 / CHAN 1 / INST 1   # channel select (auto-detected, see below)
    VOLT 5.0             # set voltage   (NOT SOURce:VOLTage:LEVel:...)
    CURR 1.0             # set current   (NOT SOURce:CURRent:LEVel:...)
    OUTP ON / OUTP OFF   # output enable, acts on the SELECTED channel
    VOLT? / CURR?        # read setpoints
    MEAS:VOLT? / MEAS:CURR?   # measure actual V / I
    SYST:ERR?            # drain error queue

Channel-select dialect
----------------------
The instrument has two remote command modes (front panel: *System →
Instructions*): **Standard** (the IT-N6300 set, channel select via
``CHANnel <n>`` / ``INSTrument <n>``) and **Extended** (IT6300-compatible,
channel select via ``INST:NSEL <n>``).  Sending the wrong form raises error
150 ("wrong parameter").  On connect this driver probes which form the
firmware accepts and uses it from then on, so it works in either mode.

Per-channel output
------------------
``OUTP ON``/``OUTP OFF`` applies to the *currently selected* channel only.
The driver re-asserts the channel selection before every command, so
toggling channel 2 never touches channels 1 and 3.

.. note::
   If all three outputs still switch together, the instrument itself is
   coupling them: check *System → Output Coupling* is **Off** (not ALL /
   CH1-CH2 / …) and *Coupling* ([Shift]+[7]) is **Standard** — those
   front-panel modes gang the outputs in hardware and cannot be overridden
   over SCPI.

Typical usage::

    from psu_control import ITN6332B, Priority

    with ITN6332B.open_tcp("192.168.200.100") as psu:
        print(psu.idn())
        psu.apply(12.0, 5.0)   # 12 V, 5 A limit on current channel
        psu.output_on()
        print(psu.measure())
"""

from __future__ import annotations

import enum
import time
from typing import Optional

from .base import BasePSUDriver, Channel, Measurement  # noqa: F401 (re-exported)
from .exceptions import PSUCommandError, PSUProtectionTripped
from .scpi import DEFAULT_SCPI_PORT, DEFAULT_TIMEOUT_S, ScpiConnection


class Priority(enum.Enum):
    """Regulation priority mode."""

    VOLTAGE = "VOLTage"
    CURRENT = "CURRent"


class FunctionMode(enum.Enum):
    """Operating mode."""

    FIXED = "FIXed"
    LIST = "LIST"
    BATTERY = "BATTery"


class ITN6332B(BasePSUDriver):
    """Driver for the ITECH IT-N6332B (3-channel) DC power supply.

    Open with :meth:`open_tcp`, :meth:`open_visa` or :meth:`open_usb`.
    Commands act on the currently selected channel (default 1).  Use
    :meth:`channel` to drive individual channels without manual selection::

        psu.channel(1).apply(12.0, 5.0)
        psu.channel(2).apply(5.0, 1.0)
        psu.channel(1).output_on()
    """

    MAX_CHANNELS = 3        # IT-N6332B has 3 independent outputs
    DEFAULT_TCP_PORT = DEFAULT_SCPI_PORT  # 30000

    # Channel-select command candidates, probed in order at connect time.
    # "INST:NSEL" is the Extended (IT6300-compatible) form; "CHAN" and "INST"
    # are the Standard IT-N6300 forms.  See the module docstring.
    _SELECT_FORMS = ("INST:NSEL", "CHAN", "INST")

    def __init__(
        self,
        connection: ScpiConnection,
        *,
        channel: int = 1,
        claim_remote: bool = True,
    ) -> None:
        super().__init__(connection)
        if claim_remote:
            try:
                self.scpi.write("SYSTem:REMote")
            except Exception:
                pass
        self._select_cmd = self._detect_select_command()
        self.select_channel(channel)

    def _detect_select_command(self) -> str:
        """Probe which channel-select command this firmware accepts.

        Writes each candidate followed by ``SYST:ERR?``; the first form that
        leaves the error queue clean wins.  Falls back to the first candidate
        if probing itself fails (e.g. an unusual transport).
        """
        try:
            self.scpi.write("*CLS")
            for form in self._SELECT_FORMS:
                self.scpi.write(f"{form} 1")
                code, _ = self.next_error()
                if code == 0:
                    return form
                while code != 0:  # drain any follow-on errors
                    code, _ = self.next_error()
        except Exception:
            pass
        return self._SELECT_FORMS[0]

    # ------------------------------------------------------------------ #
    # Constructors
    # ------------------------------------------------------------------ #

    @classmethod
    def open_tcp(
        cls,
        host: str,
        port: int = DEFAULT_SCPI_PORT,
        *,
        channel: int = 1,
        timeout: float = DEFAULT_TIMEOUT_S,
        claim_remote: bool = True,
    ) -> "ITN6332B":
        """Open over a raw TCP socket (ITECH default port 30000)."""
        return cls(
            ScpiConnection.from_tcp(host, port, timeout=timeout),
            channel=channel,
            claim_remote=claim_remote,
        )

    @classmethod
    def open_visa(
        cls,
        resource: str,
        *,
        channel: int = 1,
        timeout: float = DEFAULT_TIMEOUT_S,
        visa_library: str = "@py",
        claim_remote: bool = True,
    ) -> "ITN6332B":
        """Open through any PyVISA resource string (TCPIP/USB/GPIB/serial)."""
        return cls(
            ScpiConnection.from_visa(resource, timeout=timeout, visa_library=visa_library),
            channel=channel,
            claim_remote=claim_remote,
        )

    @classmethod
    def open_usb(
        cls,
        serial: Optional[str] = None,
        *,
        vendor_id: int = 0x2EC7,  # ITECH USB vendor ID
        product_id: Optional[int] = None,
        channel: int = 1,
        timeout: float = DEFAULT_TIMEOUT_S,
        claim_remote: bool = True,
    ) -> "ITN6332B":
        """Open over USB-TMC via PyVISA (wildcards used when unspecified)."""
        pid = f"0x{product_id:04X}" if product_id is not None else "?*"
        serial_part = serial if serial is not None else "?*"
        resource = f"USB::0x{vendor_id:04X}::{pid}::{serial_part}::INSTR"
        return cls(
            ScpiConnection.from_visa(resource, timeout=timeout),
            channel=channel,
            claim_remote=claim_remote,
        )

    # ------------------------------------------------------------------ #
    # Channel selection (auto-detected form, channels 1-3)
    # ------------------------------------------------------------------ #

    def select_channel(self, number: int) -> None:
        """Select the active channel (1-3).

        Always writes the selection (no caching): the front panel or another
        controller can change the instrument's selected channel at any time,
        and a stale cache would silently steer ``OUTP``/``VOLT``/``CURR`` at
        the wrong channel.  One short extra write per operation is cheap
        insurance for per-channel correctness.
        """
        if not 1 <= number <= self.MAX_CHANNELS:
            raise ValueError(f"channel must be in 1..{self.MAX_CHANNELS}")
        self.scpi.write(f"{self._select_cmd} {number}")
        self._channel = number

    # ------------------------------------------------------------------ #
    # Identification & housekeeping
    # ------------------------------------------------------------------ #

    def idn(self) -> str:
        """Return the ``*IDN?`` identification string."""
        return self.scpi.query("*IDN?")

    def reset(self) -> None:
        """Reset the instrument to its power-on configuration (``*RST``)."""
        self.scpi.write("*RST")
        self._channel = None
        self.wait_complete()
        self.select_channel(1)

    def clear_status(self) -> None:
        """Clear status registers and the error queue (``*CLS``)."""
        self.scpi.write("*CLS")

    def wait_complete(self, timeout: Optional[float] = None) -> None:
        """Block until pending operations finish (``*OPC?`` handshake)."""
        prev = self.scpi.timeout
        if timeout is not None:
            self.scpi.timeout = timeout
        try:
            self.scpi.query("*OPC?")
        finally:
            self.scpi.timeout = prev

    def self_test(self) -> bool:
        """Run the instrument self-test (``*TST?``); ``True`` means passed."""
        return self.scpi.query("*TST?").strip() in ("0", "+0")

    def remote(self) -> None:
        """Lock the front panel and accept remote commands."""
        self.scpi.write("SYSTem:REMote")

    def local(self) -> None:
        """Return to local (front-panel) control."""
        self.scpi.write("SYSTem:LOCal")

    def lock_local(self, locked: bool = True) -> None:
        """Lock out the LOCAL key so it can't drop remote."""
        self.scpi.write("SYSTem:RWLock" if locked else "SYSTem:LOCal")

    def beep(self) -> None:
        """Emit a beep."""
        self.scpi.write("SYSTem:BEEPer:IMMediate")

    def firmware_version(self) -> str:
        """Return the SCPI version."""
        return self.scpi.query("SYSTem:VERSion?")

    # ------------------------------------------------------------------ #
    # Error queue
    # ------------------------------------------------------------------ #

    def next_error(self) -> tuple[int, str]:
        """Pop one entry from the error queue (``SYST:ERR?``)."""
        resp = self.scpi.query("SYST:ERR?")
        code_str, _, msg = resp.partition(",")
        try:
            code = int(code_str)
        except ValueError:
            code = -1
        return code, msg.strip().strip('"')

    def check_errors(self) -> None:
        """Drain the error queue, raising :class:`PSUCommandError` on any error."""
        code, msg = self.next_error()
        if code != 0:
            first = (code, msg)
            while True:
                nxt_code, _ = self.next_error()
                if nxt_code == 0:
                    break
            raise PSUCommandError(first[0], first[1])

    # ------------------------------------------------------------------ #
    # Regulation priority
    # ------------------------------------------------------------------ #

    def set_priority(self, priority) -> None:
        """Select constant-voltage or constant-current priority.

        Accepts a :class:`Priority` enum *or* a string (``"VOLTAGE"`` /
        ``"CURRENT"`` / ``"CV"`` / ``"CC"``).
        """
        if isinstance(priority, Priority):
            val = priority.value
        else:
            s = str(priority).upper()
            if s in ("CV", "VOLTAGE"):
                val = Priority.VOLTAGE.value
            elif s in ("CC", "CURRENT"):
                val = Priority.CURRENT.value
            else:
                val = Priority[s].value
        self.scpi.write(f"SOURce:FUNCtion:PRIority {val}")

    def get_priority(self) -> str:
        """Return the active priority as ``"VOLTAGE"`` or ``"CURRENT"``."""
        resp = self.scpi.query("SOURce:FUNCtion:PRIority?").strip().upper()
        return "CURRENT" if resp.startswith("CURR") else "VOLTAGE"

    def set_function_mode(self, mode: FunctionMode) -> None:
        """Set the operating mode: FIXED, LIST or BATTERY."""
        self.scpi.write(f"SOURce:FUNCtion:MODE {mode.value}")

    # ------------------------------------------------------------------ #
    # Voltage
    # ------------------------------------------------------------------ #

    def set_voltage(self, volts: float) -> None:
        """Set the output voltage setpoint (V)."""
        self.scpi.write(f"VOLT {volts:.6g}")

    def get_voltage(self) -> float:
        """Return the programmed voltage setpoint (V)."""
        return float(self.scpi.query("VOLT?"))

    def voltage_range(self) -> tuple[float, float]:
        """Return the (min, max) programmable voltage reported by the device."""
        return self._range("VOLT")

    def set_voltage_limits(
        self, high: Optional[float] = None, low: Optional[float] = None
    ) -> None:
        """Set the soft voltage limits."""
        if high is not None:
            self.scpi.write(f"SOURce:VOLTage:LEVel:LIMit:HIGH {high:.6g}")
        if low is not None:
            self.scpi.write(f"SOURce:VOLTage:LEVel:LIMit:LOW {low:.6g}")

    def set_voltage_slew(
        self,
        both: Optional[float] = None,
        *,
        positive: Optional[float] = None,
        negative: Optional[float] = None,
    ) -> None:
        """Set the voltage slew (ramp) rate in V/s."""
        if both is not None:
            self.scpi.write(f"SOURce:VOLTage:SLEW:BOTH {both:.6g}")
        if positive is not None:
            self.scpi.write(f"SOURce:VOLTage:SLEW:POSitive {positive:.6g}")
        if negative is not None:
            self.scpi.write(f"SOURce:VOLTage:SLEW:NEGative {negative:.6g}")

    # ------------------------------------------------------------------ #
    # Current
    # ------------------------------------------------------------------ #

    def set_current(self, amps: float) -> None:
        """Set the current setpoint / limit (A)."""
        self.scpi.write(f"CURR {amps:.6g}")

    def get_current(self) -> float:
        """Return the programmed current setpoint (A)."""
        return float(self.scpi.query("CURR?"))

    def current_range(self) -> tuple[float, float]:
        """Return the (min, max) programmable current reported by the device.

        For a bidirectional unit the minimum is negative (the sink limit).
        """
        return self._range("CURR")

    def set_current_slew(
        self,
        both: Optional[float] = None,
        *,
        positive: Optional[float] = None,
        negative: Optional[float] = None,
    ) -> None:
        """Set the current slew (ramp) rate in A/s."""
        if both is not None:
            self.scpi.write(f"SOURce:CURRent:SLEW:BOTH {both:.6g}")
        if positive is not None:
            self.scpi.write(f"SOURce:CURRent:SLEW:POSitive {positive:.6g}")
        if negative is not None:
            self.scpi.write(f"SOURce:CURRent:SLEW:NEGative {negative:.6g}")

    # ------------------------------------------------------------------ #
    # Power
    # ------------------------------------------------------------------ #

    def set_power(self, watts: float) -> None:
        """Set the power setpoint (W)."""
        self.scpi.write(f"SOURce:POWer:LEVel:IMMediate:AMPLitude {watts:.6g}")

    def get_power(self) -> float:
        """Return the programmed power setpoint (W)."""
        return float(self.scpi.query("SOURce:POWer:LEVel:IMMediate:AMPLitude?"))

    # ------------------------------------------------------------------ #
    # Combined setpoint
    # ------------------------------------------------------------------ #

    def apply(self, voltage: float, current: float) -> None:
        """Set voltage and current setpoints."""
        self.scpi.write(f"VOLT {voltage:.6g}")
        self.scpi.write(f"CURR {current:.6g}")

    # ------------------------------------------------------------------ #
    # Output control
    # ------------------------------------------------------------------ #

    def output_on(self) -> None:
        """Enable the output."""
        self.scpi.write("OUTP ON")

    def output_off(self) -> None:
        """Disable the output."""
        self.scpi.write("OUTP OFF")

    def set_output(self, enabled: bool) -> None:
        self.scpi.write(f"OUTP {'ON' if enabled else 'OFF'}")

    @property
    def output_enabled(self) -> bool:
        """Whether the output is currently enabled."""
        return self.scpi.query("OUTP?").strip() in ("1", "ON")

    def set_output_delays(
        self, on_s: Optional[float] = None, off_s: Optional[float] = None
    ) -> None:
        """Set output on/off sequencing delays in seconds."""
        if on_s is not None:
            self.scpi.write(f"OUTPut:DELay:ON {on_s:.6g}")
        if off_s is not None:
            self.scpi.write(f"OUTPut:DELay:OFF {off_s:.6g}")

    # ------------------------------------------------------------------ #
    # Protections
    # ------------------------------------------------------------------ #

    def set_ovp(
        self, volts: float, *, enable: bool = True, delay_s: Optional[float] = None
    ) -> None:
        """Configure over-voltage protection (V)."""
        self.scpi.write(f"SOURce:VOLTage:PROTection:LEVel {volts:.6g}")
        if delay_s is not None:
            self.scpi.write(f"SOURce:VOLTage:PROTection:DELay {delay_s:.6g}")
        self.scpi.write(f"SOURce:VOLTage:PROTection:STATe {'ON' if enable else 'OFF'}")

    def set_uvp(self, volts: float, *, enable: bool = True) -> None:
        """Configure under-voltage protection (V)."""
        self.scpi.write(f"SOURce:VOLTage:UNDer:PROTection:LEVel {volts:.6g}")
        self.scpi.write(f"SOURce:VOLTage:UNDer:PROTection:STATe {'ON' if enable else 'OFF'}")

    def set_ocp(
        self, amps: float, *, enable: bool = True, delay_s: Optional[float] = None
    ) -> None:
        """Configure over-current protection (A)."""
        self.scpi.write(f"SOURce:CURRent:OVER:PROTection:LEVel {amps:.6g}")
        if delay_s is not None:
            self.scpi.write(f"SOURce:CURRent:OVER:PROTection:DELay {delay_s:.6g}")
        self.scpi.write(f"SOURce:CURRent:OVER:PROTection:STATe {'ON' if enable else 'OFF'}")

    def set_ucp(self, amps: float, *, enable: bool = True) -> None:
        """Configure under-current protection (A)."""
        self.scpi.write(f"SOURce:CURRent:UNDer:PROTection:LEVel {amps:.6g}")
        self.scpi.write(f"SOURce:CURRent:UNDer:PROTection:STATe {'ON' if enable else 'OFF'}")

    def set_opp(
        self, watts: float, *, enable: bool = True, delay_s: Optional[float] = None
    ) -> None:
        """Configure over-power protection (W)."""
        self.scpi.write(f"SOURce:POWer:PROTection:LEVel {watts:.6g}")
        if delay_s is not None:
            self.scpi.write(f"SOURce:POWer:PROTection:DELay {delay_s:.6g}")
        self.scpi.write(f"SOURce:POWer:PROTection:STATe {'ON' if enable else 'OFF'}")

    def clear_protection(self) -> None:
        """Clear any latched protection."""
        self.scpi.write("OUTPut:PROTection:CLEar")

    def questionable_condition(self) -> int:
        """Return the raw Questionable Status condition register value."""
        return int(self.scpi.query("STATus:QUEStionable:CONDition?"))

    def protection_tripped(self) -> bool:
        """Whether any questionable-status (protection/fault) flag is set."""
        return self.questionable_condition() != 0

    def raise_if_tripped(self) -> None:
        """Raise :class:`PSUProtectionTripped` if any protection has tripped."""
        cond = self.questionable_condition()
        if cond != 0:
            raise PSUProtectionTripped(f"Questionable status condition = 0x{cond:04X}")

    # ------------------------------------------------------------------ #
    # Measurements
    # ------------------------------------------------------------------ #

    def measure_voltage(self) -> float:
        """Measure the actual output voltage (V)."""
        return float(self.scpi.query("MEAS:VOLT?"))

    def measure_current(self) -> float:
        """Measure the actual output current (A); negative while sinking."""
        return float(self.scpi.query("MEAS:CURR?"))

    # measure_power() and measure() are inherited from BasePSUDriver (V × I)

    # ------------------------------------------------------------------ #
    # Saved states
    # ------------------------------------------------------------------ #

    def save_state(self, slot: int) -> None:
        """Save the current setup to non-volatile memory slot (``*SAV``)."""
        self.scpi.write(f"*SAV {int(slot)}")

    def recall_state(self, slot: int) -> None:
        """Recall a saved setup from memory slot (``*RCL``)."""
        self.scpi.write(f"*RCL {int(slot)}")
        self._channel = None
        self.select_channel(1)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _range(self, base: str) -> tuple[float, float]:
        """Query a setpoint's MIN/MAX, restoring the previous value afterwards."""
        prev = self.scpi.query(f"{base}?")
        lo = float(self.scpi.query(f"{base}? MIN"))
        hi = float(self.scpi.query(f"{base}? MAX"))
        self.scpi.write(f"{base} {prev}")  # MIN/MAX queries can move the setpoint
        return lo, hi

    def ramp_voltage(
        self, target: float, *, step: float = 0.5, interval_s: float = 0.1
    ) -> None:
        """Software-ramp the voltage setpoint to ``target`` in steps."""
        if step <= 0:
            raise ValueError("step must be positive")
        start = self.get_voltage()
        n = max(1, int(abs(target - start) / step))
        for i in range(1, n + 1):
            self.set_voltage(start + (target - start) * i / n)
            time.sleep(interval_s)
        self.set_voltage(target)

    def __repr__(self) -> str:
        return f"ITN6332B(ch={self.selected_channel}, {self.scpi!r})"
