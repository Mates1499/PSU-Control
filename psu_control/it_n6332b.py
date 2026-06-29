"""Driver for the ITECH IT-N6332B bidirectional DC power supply.

The IT-N6332B uses ITECH's unified SCPI command set (documented in the
IT-M3100 / IT-N6300 Programming Guide, included in ``docs/``). It is a
**bidirectional** supply -- it can source *and* sink power -- and supports:

* CV / CC **priority** selection (``[SOURce:]FUNCtion:PRIority``),
* independent positive/negative voltage & current **slew** rates,
* a full protection suite: OVP / OCP / OPP plus under-voltage / under-current,
* channel addressing (``CHANnel <n>``, 1--16) for multi-unit / parallel systems,
* device-reported ranges via SCPI ``MIN`` / ``MAX`` queries.

Typical usage::

    from psu_control import ITN6332B, Priority

    with ITN6332B.open_tcp("192.168.1.50") as psu:
        print(psu.idn())
        psu.reset()
        psu.set_priority(Priority.VOLTAGE)   # CV priority
        psu.apply(12.0, 5.0)                 # 12 V, 5 A limit
        psu.set_ovp(13.5)
        psu.output_on()
        print(psu.measure())                 # 12.00 V, 0.50 A, 6.0 W

Command mnemonics are taken verbatim from the Programming Guide. The raw
connection is always available via ``psu.scpi.write`` / ``psu.scpi.query``.
"""

from __future__ import annotations

import enum
import time
from typing import Optional

from .base import BasePSUDriver, Channel, Measurement  # noqa: F401 (re-exported)
from .exceptions import PSUCommandError, PSUProtectionTripped
from .scpi import DEFAULT_SCPI_PORT, DEFAULT_TIMEOUT_S, ScpiConnection


class Priority(enum.Enum):
    """Regulation priority mode (``[SOURce:]FUNCtion:PRIority``)."""

    VOLTAGE = "VOLTage"
    CURRENT = "CURRent"


class FunctionMode(enum.Enum):
    """Operating mode (``[SOURce:]FUNCtion:MODE``)."""

    FIXED = "FIXed"
    LIST = "LIST"
    BATTERY = "BATTery"


class ITN6332B(BasePSUDriver):
    """Driver for an ITECH IT-N6332B bidirectional DC power supply.

    Open with :meth:`open_tcp`, :meth:`open_visa` or :meth:`open_usb`. Commands
    act on the currently selected channel (default 1). For multi-channel /
    multi-unit (paralleled) systems, use :meth:`channel` to get a
    :class:`~psu_control.Channel` proxy per channel, or :meth:`select_channel`
    to switch the active channel for the driver's own methods.
    """

    MAX_CHANNELS = 16       # CHANnel <n> accepts 1..16 per the programming guide
    DEFAULT_TCP_PORT = DEFAULT_SCPI_PORT  # 30000

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
        self.select_channel(channel)

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
        """Open over a raw TCP socket (ITECH default SCPI port is 30000)."""
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
    # Channel selection (CHANnel <n>, 1-16)
    # ------------------------------------------------------------------ #

    def select_channel(self, number: int) -> None:
        """Select the active channel (1-16); cached to avoid redundant writes."""
        if not 1 <= number <= self.MAX_CHANNELS:
            raise ValueError("channel must be in 1..16")
        if self._channel != number:
            self.scpi.write(f"CHANnel {number}")
            self._channel = number

    def channel_available(self, number: int) -> bool:
        """Return whether the instrument for the given channel is present."""
        return self.scpi.query(f"CHANnel:STATe? {number}").strip() in ("1", "ON")

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
        """Lock the front panel and accept remote commands (``SYSTem:REMote``)."""
        self.scpi.write("SYSTem:REMote")

    def local(self) -> None:
        """Return to local (front-panel) control (``SYSTem:LOCal``)."""
        self.scpi.write("SYSTem:LOCal")

    def lock_local(self, locked: bool = True) -> None:
        """Lock out the LOCAL key (``SYSTem:RWLock``) so it can't drop remote."""
        self.scpi.write("SYSTem:RWLock" if locked else "SYSTem:LOCal")

    def beep(self) -> None:
        """Emit a beep (``SYSTem:BEEPer:IMMediate``)."""
        self.scpi.write("SYSTem:BEEPer:IMMediate")

    def firmware_version(self) -> str:
        """Return the SCPI version (``SYSTem:VERSion?``)."""
        return self.scpi.query("SYSTem:VERSion?")

    # ------------------------------------------------------------------ #
    # Error queue
    # ------------------------------------------------------------------ #

    def next_error(self) -> tuple[int, str]:
        """Pop one entry from the error queue (``SYSTem:ERRor?``)."""
        resp = self.scpi.query("SYSTem:ERRor?")
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
    # Regulation mode
    # ------------------------------------------------------------------ #

    def set_priority(self, priority) -> None:
        """Select constant-voltage or constant-current priority.

        Accepts a :class:`Priority` enum *or* a string (``\"VOLTAGE\"`` /
        ``\"CURRENT\"`` / ``\"CV\"`` / ``\"CC\"``).
        """
        if isinstance(priority, Priority):
            val = priority.value
        else:
            s = str(priority).upper()
            # Accept both "VOLTAGE"/"CURRENT" and "CV"/"CC" shorthand
            if s in ("CV", "VOLTAGE"):
                val = Priority.VOLTAGE.value
            elif s in ("CC", "CURRENT"):
                val = Priority.CURRENT.value
            else:
                val = Priority[s].value
        self.scpi.write(f"SOURce:FUNCtion:PRIority {val}")

    def get_priority(self) -> str:
        """Return the active priority as ``\"VOLTAGE\"`` or ``\"CURRENT\"``."""
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
        self.scpi.write(f"SOURce:VOLTage:LEVel:IMMediate:AMPLitude {volts}")

    def get_voltage(self) -> float:
        """Return the programmed voltage setpoint (V)."""
        return float(self.scpi.query("SOURce:VOLTage:LEVel:IMMediate:AMPLitude?"))

    def voltage_range(self) -> tuple[float, float]:
        """Return the (min, max) programmable voltage reported by the device."""
        return self._range("SOURce:VOLTage:LEVel:IMMediate:AMPLitude")

    def set_voltage_limits(
        self, high: Optional[float] = None, low: Optional[float] = None
    ) -> None:
        """Set the soft voltage limits (``VOLTage:LEVel:LIMit:HIGH|LOW``)."""
        if high is not None:
            self.scpi.write(f"SOURce:VOLTage:LEVel:LIMit:HIGH {high}")
        if low is not None:
            self.scpi.write(f"SOURce:VOLTage:LEVel:LIMit:LOW {low}")

    def set_voltage_slew(
        self,
        both: Optional[float] = None,
        *,
        positive: Optional[float] = None,
        negative: Optional[float] = None,
    ) -> None:
        """Set the voltage slew (ramp) rate in V/s (both, or per direction)."""
        if both is not None:
            self.scpi.write(f"SOURce:VOLTage:SLEW:BOTH {both}")
        if positive is not None:
            self.scpi.write(f"SOURce:VOLTage:SLEW:POSitive {positive}")
        if negative is not None:
            self.scpi.write(f"SOURce:VOLTage:SLEW:NEGative {negative}")

    # ------------------------------------------------------------------ #
    # Current
    # ------------------------------------------------------------------ #

    def set_current(self, amps: float) -> None:
        """Set the current setpoint / limit (A)."""
        self.scpi.write(f"SOURce:CURRent:LEVel:IMMediate:AMPLitude {amps}")

    def get_current(self) -> float:
        """Return the programmed current setpoint (A)."""
        return float(self.scpi.query("SOURce:CURRent:LEVel:IMMediate:AMPLitude?"))

    def current_range(self) -> tuple[float, float]:
        """Return the (min, max) programmable current reported by the device.

        For a bidirectional unit the minimum is negative (the sink limit).
        """
        return self._range("SOURce:CURRent:LEVel:IMMediate:AMPLitude")

    def set_current_slew(
        self,
        both: Optional[float] = None,
        *,
        positive: Optional[float] = None,
        negative: Optional[float] = None,
    ) -> None:
        """Set the current slew (ramp) rate in A/s (both, or per direction)."""
        if both is not None:
            self.scpi.write(f"SOURce:CURRent:SLEW:BOTH {both}")
        if positive is not None:
            self.scpi.write(f"SOURce:CURRent:SLEW:POSitive {positive}")
        if negative is not None:
            self.scpi.write(f"SOURce:CURRent:SLEW:NEGative {negative}")

    # ------------------------------------------------------------------ #
    # Power
    # ------------------------------------------------------------------ #

    def set_power(self, watts: float) -> None:
        """Set the power setpoint (``POWer:LEVel:IMMediate:AMPLitude``)."""
        self.scpi.write(f"SOURce:POWer:LEVel:IMMediate:AMPLitude {watts}")

    def get_power(self) -> float:
        """Return the programmed power setpoint (W)."""
        return float(self.scpi.query("SOURce:POWer:LEVel:IMMediate:AMPLitude?"))

    # ------------------------------------------------------------------ #
    # Combined setpoint
    # ------------------------------------------------------------------ #

    def apply(self, voltage: float, current: float) -> None:
        """Set voltage and current together (``APPLy <v>,<a>``)."""
        self.scpi.write(f"SOURce:APPLy {voltage},{current}")

    # ------------------------------------------------------------------ #
    # Output control
    # ------------------------------------------------------------------ #

    def output_on(self) -> None:
        """Enable the output."""
        self.scpi.write("OUTPut:STATe ON")

    def output_off(self) -> None:
        """Disable the output."""
        self.scpi.write("OUTPut:STATe OFF")

    def set_output(self, enabled: bool) -> None:
        self.scpi.write(f"OUTPut:STATe {'ON' if enabled else 'OFF'}")

    @property
    def output_enabled(self) -> bool:
        """Whether the output is currently enabled."""
        return self.scpi.query("OUTPut:STATe?").strip() in ("1", "ON")

    def set_output_delays(
        self, on_s: Optional[float] = None, off_s: Optional[float] = None
    ) -> None:
        """Set output on/off sequencing delays in seconds."""
        if on_s is not None:
            self.scpi.write(f"OUTPut:DELay:ON {on_s}")
        if off_s is not None:
            self.scpi.write(f"OUTPut:DELay:OFF {off_s}")

    # ------------------------------------------------------------------ #
    # Protections
    # ------------------------------------------------------------------ #

    def set_ovp(
        self, volts: float, *, enable: bool = True, delay_s: Optional[float] = None
    ) -> None:
        """Configure over-voltage protection (V)."""
        self.scpi.write(f"SOURce:VOLTage:PROTection:LEVel {volts}")
        if delay_s is not None:
            self.scpi.write(f"SOURce:VOLTage:PROTection:DELay {delay_s}")
        self.scpi.write(f"SOURce:VOLTage:PROTection:STATe {'ON' if enable else 'OFF'}")

    def set_uvp(self, volts: float, *, enable: bool = True) -> None:
        """Configure under-voltage protection (V)."""
        self.scpi.write(f"SOURce:VOLTage:UNDer:PROTection:LEVel {volts}")
        self.scpi.write(f"SOURce:VOLTage:UNDer:PROTection:STATe {'ON' if enable else 'OFF'}")

    def set_ocp(
        self, amps: float, *, enable: bool = True, delay_s: Optional[float] = None
    ) -> None:
        """Configure over-current protection (A)."""
        self.scpi.write(f"SOURce:CURRent:OVER:PROTection:LEVel {amps}")
        if delay_s is not None:
            self.scpi.write(f"SOURce:CURRent:OVER:PROTection:DELay {delay_s}")
        self.scpi.write(f"SOURce:CURRent:OVER:PROTection:STATe {'ON' if enable else 'OFF'}")

    def set_ucp(self, amps: float, *, enable: bool = True) -> None:
        """Configure under-current protection (A)."""
        self.scpi.write(f"SOURce:CURRent:UNDer:PROTection:LEVel {amps}")
        self.scpi.write(f"SOURce:CURRent:UNDer:PROTection:STATe {'ON' if enable else 'OFF'}")

    def set_opp(
        self, watts: float, *, enable: bool = True, delay_s: Optional[float] = None
    ) -> None:
        """Configure over-power protection (W)."""
        self.scpi.write(f"SOURce:POWer:PROTection:LEVel {watts}")
        if delay_s is not None:
            self.scpi.write(f"SOURce:POWer:PROTection:DELay {delay_s}")
        self.scpi.write(f"SOURce:POWer:PROTection:STATe {'ON' if enable else 'OFF'}")

    def clear_protection(self) -> None:
        """Clear any latched protection (``OUTPut:PROTection:CLEar``)."""
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
        return float(self.scpi.query("MEASure:SCALar:VOLTage:DC?"))

    def measure_current(self) -> float:
        """Measure the actual output current (A); negative while sinking."""
        return float(self.scpi.query("MEASure:SCALar:CURRent:DC?"))

    def measure_power(self) -> float:
        """Measure the actual output power (W); negative while sinking."""
        return float(self.scpi.query("MEASure:SCALar:POWer:DC?"))

    def measure(self) -> Measurement:
        """Return a single :class:`Measurement` snapshot of V, I and P."""
        return Measurement(
            voltage=self.measure_voltage(),
            current=self.measure_current(),
            power=self.measure_power(),
        )

    # ------------------------------------------------------------------ #
    # Saved states
    # ------------------------------------------------------------------ #

    def save_state(self, slot: int) -> None:
        """Save the current setup to non-volatile memory ``slot`` (``*SAV``)."""
        self.scpi.write(f"*SAV {int(slot)}")

    def recall_state(self, slot: int) -> None:
        """Recall a saved setup from memory ``slot`` (``*RCL``)."""
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
        """Software-ramp the voltage setpoint to ``target`` in software steps."""
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
