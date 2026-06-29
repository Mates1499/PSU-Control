"""High-level driver for the ITECH IT-N6332B bidirectional DC power supply.

The IT-N6332B is a two-quadrant (source + sink) programmable DC supply from
ITECH's IT-N6300 series. It is SCPI-controllable over LAN, USB-TMC and GPIB.
Because it is bidirectional, current and power limits are defined separately
for the *positive* (sourcing) and *negative* (sinking) directions.

This driver wraps :class:`~psu_control.scpi.ScpiConnection` and exposes an
ergonomic, fully type-hinted Python API for every common operation: output
control, CV/CC priority modes, voltage/current/power setpoints, slew rates,
the full protection suite (OVP/OCP/OPP and over-temperature read-back),
measurements, status polling, and saved-state recall.

SCPI mnemonics follow the IT-N6300 series programming reference. Where a model
or firmware revision uses a slightly different mnemonic, the raw
:meth:`~psu_control.scpi.ScpiConnection.write` / ``query`` methods on
``psu.scpi`` remain available as an escape hatch.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass
from typing import Optional, Sequence

from .exceptions import PSUCommandError, PSUProtectionTripped
from .scpi import DEFAULT_SCPI_PORT, DEFAULT_TIMEOUT_S, ScpiConnection


class OutputMode(enum.Enum):
    """Source priority / regulation mode of the supply.

    * ``VOLTAGE`` -- constant-voltage priority (the default for a PSU).
    * ``CURRENT`` -- constant-current priority.
    """

    VOLTAGE = "VOLT"
    CURRENT = "CURR"


@dataclass(frozen=True)
class Measurement:
    """A simultaneous snapshot of the output, as returned by :meth:`measure_all`."""

    voltage: float  # Volts
    current: float  # Amps (positive = sourcing, negative = sinking)
    power: float    # Watts

    def __str__(self) -> str:
        return f"{self.voltage:.4f} V, {self.current:.4f} A, {self.power:.4f} W"


@dataclass(frozen=True)
class ProtectionStatus:
    """Decoded protection / fault flags from the questionable status register."""

    ovp: bool   # over-voltage protection tripped
    ocp: bool   # over-current protection tripped
    opp: bool   # over-power protection tripped
    otp: bool   # over-temperature protection tripped
    raw: int    # raw questionable-status register value

    @property
    def any_tripped(self) -> bool:
        return self.ovp or self.ocp or self.opp or self.otp

    def __str__(self) -> str:
        if not self.any_tripped:
            return "OK (no protection tripped)"
        tripped = [
            name
            for name, flag in (
                ("OVP", self.ovp),
                ("OCP", self.ocp),
                ("OPP", self.opp),
                ("OTP", self.otp),
            )
            if flag
        ]
        return "TRIPPED: " + ", ".join(tripped)


# Bit masks in the SCPI Questionable Status register used by ITECH supplies.
_QUES_OV = 0x0001   # over-voltage
_QUES_OC = 0x0002   # over-current
_QUES_OP = 0x0008   # over-power
_QUES_OT = 0x0010   # over-temperature


class ITN6332B:
    """Driver for an ITECH IT-N6332B power supply.

    Construct via the :meth:`open_tcp`, :meth:`open_visa` or :meth:`open_usb`
    classmethods, or wrap an existing :class:`ScpiConnection`::

        with ITN6332B.open_tcp("192.168.1.50") as psu:
            psu.set_voltage(12.0)
            psu.output_on()
    """

    def __init__(self, connection: ScpiConnection, *, claim_remote: bool = True):
        self.scpi = connection
        if claim_remote:
            # Put the instrument under remote control so the front panel is
            # locked out and commands are accepted. Ignored by models that
            # don't implement it.
            try:
                self.scpi.write("SYSTem:REMote")
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Constructors
    # ------------------------------------------------------------------ #

    @classmethod
    def open_tcp(
        cls,
        host: str,
        port: int = DEFAULT_SCPI_PORT,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        claim_remote: bool = True,
    ) -> "ITN6332B":
        """Open over a raw TCP socket (no VISA install required)."""
        return cls(
            ScpiConnection.from_tcp(host, port, timeout=timeout),
            claim_remote=claim_remote,
        )

    @classmethod
    def open_visa(
        cls,
        resource: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        visa_library: str = "@py",
        claim_remote: bool = True,
    ) -> "ITN6332B":
        """Open through any PyVISA resource string (TCPIP/USB/GPIB/serial)."""
        return cls(
            ScpiConnection.from_visa(
                resource, timeout=timeout, visa_library=visa_library
            ),
            claim_remote=claim_remote,
        )

    @classmethod
    def open_usb(
        cls,
        serial: Optional[str] = None,
        *,
        vendor_id: int = 0x2EC7,  # ITECH USB vendor ID
        product_id: int = 0x6300,  # IT-N6300 series product ID
        timeout: float = DEFAULT_TIMEOUT_S,
        claim_remote: bool = True,
    ) -> "ITN6332B":
        """Open over USB-TMC via PyVISA.

        If ``serial`` is omitted, a wildcard VISA pattern is used and the first
        matching instrument is opened.
        """
        serial_part = serial if serial is not None else "?*"
        resource = (
            f"USB::0x{vendor_id:04X}::0x{product_id:04X}::{serial_part}::INSTR"
        )
        return cls(
            ScpiConnection.from_visa(resource, timeout=timeout),
            claim_remote=claim_remote,
        )

    # ------------------------------------------------------------------ #
    # Identification & housekeeping
    # ------------------------------------------------------------------ #

    def idn(self) -> str:
        """Return the ``*IDN?`` identification string."""
        return self.scpi.query("*IDN?")

    def reset(self) -> None:
        """Reset the instrument to its power-on configuration (``*RST``)."""
        self.scpi.write("*RST")
        self.wait_complete()

    def clear_status(self) -> None:
        """Clear status registers and the error queue (``*CLS``)."""
        self.scpi.write("*CLS")

    def wait_complete(self, timeout: Optional[float] = None) -> None:
        """Block until pending operations finish, using the ``*OPC?`` handshake."""
        prev = self.scpi.timeout
        if timeout is not None:
            self.scpi.timeout = timeout
        try:
            self.scpi.query("*OPC?")
        finally:
            self.scpi.timeout = prev

    def self_test(self) -> bool:
        """Run the instrument self-test (``*TST?``); ``True`` means it passed."""
        return self.scpi.query("*TST?").strip() in ("0", "+0")

    def remote(self) -> None:
        """Lock the front panel and accept remote commands."""
        self.scpi.write("SYSTem:REMote")

    def local(self) -> None:
        """Return the instrument to local (front-panel) control."""
        self.scpi.write("SYSTem:LOCal")

    def beep(self) -> None:
        """Emit a beep from the instrument."""
        self.scpi.write("SYSTem:BEEPer:IMMediate")

    # ------------------------------------------------------------------ #
    # Error queue
    # ------------------------------------------------------------------ #

    def next_error(self) -> tuple[int, str]:
        """Pop one entry from the instrument error queue.

        Returns ``(0, "No error")`` when the queue is empty.
        """
        resp = self.scpi.query("SYSTem:ERRor?")
        code_str, _, msg = resp.partition(",")
        try:
            code = int(code_str)
        except ValueError:
            code = -1
        return code, msg.strip().strip('"')

    def check_errors(self) -> None:
        """Drain the error queue and raise :class:`PSUCommandError` on any error."""
        code, msg = self.next_error()
        if code != 0:
            # Drain remaining entries so the queue is clean for next time.
            first = (code, msg)
            while True:
                nxt_code, _ = self.next_error()
                if nxt_code == 0:
                    break
            raise PSUCommandError(first[0], first[1])

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
        """Enable or disable the output from a boolean."""
        self.scpi.write(f"OUTPut:STATe {'ON' if enabled else 'OFF'}")

    @property
    def output_enabled(self) -> bool:
        """Whether the output is currently enabled."""
        return self.scpi.query("OUTPut:STATe?").strip() in ("1", "ON")

    def set_output_delays(
        self, rise_s: Optional[float] = None, fall_s: Optional[float] = None
    ) -> None:
        """Set output on/off sequencing delays in seconds."""
        if rise_s is not None:
            self.scpi.write(f"OUTPut:DELay:RISE {rise_s}")
        if fall_s is not None:
            self.scpi.write(f"OUTPut:DELay:FALL {fall_s}")

    # ------------------------------------------------------------------ #
    # Regulation mode
    # ------------------------------------------------------------------ #

    def set_mode(self, mode: OutputMode) -> None:
        """Select constant-voltage or constant-current priority mode."""
        self.scpi.write(f"SOURce:FUNCtion {mode.value}")

    def get_mode(self) -> OutputMode:
        """Return the active priority mode."""
        resp = self.scpi.query("SOURce:FUNCtion?").strip().upper()
        return OutputMode.CURRENT if resp.startswith("CURR") else OutputMode.VOLTAGE

    # ------------------------------------------------------------------ #
    # Voltage
    # ------------------------------------------------------------------ #

    def set_voltage(self, volts: float) -> None:
        """Set the output voltage setpoint (V)."""
        self.scpi.write(f"SOURce:VOLTage:LEVel:IMMediate:AMPLitude {volts}")

    def get_voltage(self) -> float:
        """Return the programmed voltage setpoint (V)."""
        return float(self.scpi.query("SOURce:VOLTage:LEVel:IMMediate:AMPLitude?"))

    def set_voltage_slew(self, volts_per_s: float) -> None:
        """Set the voltage slew (ramp) rate in V/s."""
        self.scpi.write(f"SOURce:VOLTage:SLEW:IMMediate {volts_per_s}")

    def set_voltage_limits(
        self, positive: Optional[float] = None, negative: Optional[float] = None
    ) -> None:
        """Constrain the programmable voltage range (V)."""
        if positive is not None:
            self.scpi.write(f"SOURce:VOLTage:LIMit:POSitive {positive}")
        if negative is not None:
            self.scpi.write(f"SOURce:VOLTage:LIMit:NEGative {negative}")

    # ------------------------------------------------------------------ #
    # Current  (bidirectional: positive = source, negative = sink)
    # ------------------------------------------------------------------ #

    def set_current_limit(
        self,
        amps: Optional[float] = None,
        *,
        positive: Optional[float] = None,
        negative: Optional[float] = None,
    ) -> None:
        """Set the current limit(s) in amps.

        Because the IT-N6332B is bidirectional, source (positive) and sink
        (negative) limits are independent. Passing ``amps`` sets a symmetric
        limit (``+amps`` sourcing, ``-amps`` sinking). Pass ``positive`` and/or
        ``negative`` explicitly for an asymmetric configuration.
        """
        if amps is not None:
            mag = abs(amps)
            self.scpi.write(f"SOURce:CURRent:LIMit:POSitive {mag}")
            self.scpi.write(f"SOURce:CURRent:LIMit:NEGative {-mag}")
        if positive is not None:
            self.scpi.write(f"SOURce:CURRent:LIMit:POSitive {abs(positive)}")
        if negative is not None:
            self.scpi.write(f"SOURce:CURRent:LIMit:NEGative {-abs(negative)}")

    def set_current(self, amps: float) -> None:
        """Set the current setpoint used in constant-current priority mode (A)."""
        self.scpi.write(f"SOURce:CURRent:LEVel:IMMediate:AMPLitude {amps}")

    def get_current_setpoint(self) -> float:
        """Return the programmed current setpoint (A)."""
        return float(self.scpi.query("SOURce:CURRent:LEVel:IMMediate:AMPLitude?"))

    def get_current_limits(self) -> tuple[float, float]:
        """Return the ``(positive, negative)`` current limits in amps."""
        pos = float(self.scpi.query("SOURce:CURRent:LIMit:POSitive?"))
        neg = float(self.scpi.query("SOURce:CURRent:LIMit:NEGative?"))
        return pos, neg

    def set_current_slew(self, amps_per_s: float) -> None:
        """Set the current slew (ramp) rate in A/s."""
        self.scpi.write(f"SOURce:CURRent:SLEW:IMMediate {amps_per_s}")

    # ------------------------------------------------------------------ #
    # Power (bidirectional limits)
    # ------------------------------------------------------------------ #

    def set_power_limits(
        self, positive: Optional[float] = None, negative: Optional[float] = None
    ) -> None:
        """Set source (positive) and/or sink (negative) power limits in watts."""
        if positive is not None:
            self.scpi.write(f"SOURce:POWer:LIMit:POSitive {abs(positive)}")
        if negative is not None:
            self.scpi.write(f"SOURce:POWer:LIMit:NEGative {-abs(negative)}")

    # ------------------------------------------------------------------ #
    # Protections
    # ------------------------------------------------------------------ #

    def set_ovp(self, volts: float, *, enable: bool = True) -> None:
        """Configure over-voltage protection (OVP) trip level in volts."""
        self.scpi.write(f"SOURce:VOLTage:PROTection:LEVel {volts}")
        self.scpi.write(f"SOURce:VOLTage:PROTection:STATe {'ON' if enable else 'OFF'}")

    def set_ocp(self, amps: float, *, enable: bool = True) -> None:
        """Configure over-current protection (OCP) trip level in amps."""
        self.scpi.write(f"SOURce:CURRent:PROTection:LEVel {amps}")
        self.scpi.write(f"SOURce:CURRent:PROTection:STATe {'ON' if enable else 'OFF'}")

    def set_opp(self, watts: float, *, enable: bool = True) -> None:
        """Configure over-power protection (OPP) trip level in watts."""
        self.scpi.write(f"SOURce:POWer:PROTection:LEVel {watts}")
        self.scpi.write(f"SOURce:POWer:PROTection:STATe {'ON' if enable else 'OFF'}")

    def protection_status(self) -> ProtectionStatus:
        """Read and decode the questionable-status register into trip flags."""
        raw = int(self.scpi.query("STATus:QUEStionable:CONDition?"))
        return ProtectionStatus(
            ovp=bool(raw & _QUES_OV),
            ocp=bool(raw & _QUES_OC),
            opp=bool(raw & _QUES_OP),
            otp=bool(raw & _QUES_OT),
            raw=raw,
        )

    def clear_protection(self) -> None:
        """Clear any latched protection so the output can be re-enabled."""
        self.scpi.write("OUTPut:PROTection:CLEar")

    def raise_if_tripped(self) -> None:
        """Raise :class:`PSUProtectionTripped` if any protection has tripped."""
        status = self.protection_status()
        if status.any_tripped:
            raise PSUProtectionTripped(str(status))

    # ------------------------------------------------------------------ #
    # Measurements
    # ------------------------------------------------------------------ #

    def measure_voltage(self) -> float:
        """Measure the actual output voltage (V)."""
        return float(self.scpi.query("MEASure:SCALar:VOLTage:DC?"))

    def measure_current(self) -> float:
        """Measure the actual output current (A); negative when sinking."""
        return float(self.scpi.query("MEASure:SCALar:CURRent:DC?"))

    def measure_power(self) -> float:
        """Measure the actual output power (W); negative when sinking."""
        return float(self.scpi.query("MEASure:SCALar:POWer:DC?"))

    def measure_all(self) -> Measurement:
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

    # ------------------------------------------------------------------ #
    # Convenience helpers
    # ------------------------------------------------------------------ #

    def apply(
        self,
        voltage: float,
        current_limit: float,
        *,
        output: bool = True,
    ) -> None:
        """Configure a CV setpoint with a symmetric current limit in one call.

        Args:
            voltage: Output voltage setpoint in volts.
            current_limit: Symmetric source/sink current limit in amps.
            output: If ``True`` (default), enable the output afterwards.
        """
        self.set_mode(OutputMode.VOLTAGE)
        self.set_voltage(voltage)
        self.set_current_limit(current_limit)
        if output:
            self.output_on()

    def ramp_voltage(
        self,
        target: float,
        *,
        step: float = 0.5,
        interval_s: float = 0.1,
    ) -> None:
        """Software-ramp the voltage setpoint from its current value to ``target``.

        Useful for a gentle approach when the hardware slew rate is not used.
        """
        start = self.get_voltage()
        if step <= 0:
            raise ValueError("step must be positive")
        n = max(1, int(abs(target - start) / step))
        for i in range(1, n + 1):
            self.set_voltage(start + (target - start) * i / n)
            time.sleep(interval_s)
        self.set_voltage(target)

    def shutdown(self) -> None:
        """Safely turn the output off and return the instrument to local control."""
        try:
            self.output_off()
        finally:
            self.local()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        """Close the underlying connection (does not change the output state)."""
        self.scpi.close()

    def __enter__(self) -> "ITN6332B":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # On exit, fail safe: turn the output off, release remote, then close.
        try:
            self.shutdown()
        except Exception:
            pass
        self.close()

    def __repr__(self) -> str:
        return f"ITN6332B({self.scpi!r})"
