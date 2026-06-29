"""High-level driver for the ITECH IT-N6332B programmable DC power supply.

The IT-N6332B uses ITECH's unified SCPI command set (the same one documented in
the IT-M3100 / IT-N6300 Programming Guide, included in ``docs/``). The supply is
**bidirectional** -- it can source *and* sink power -- and supports:

* constant-voltage / constant-current **priority** selection
  (``[SOURce:]FUNCtion:PRIority``),
* independent positive/negative voltage & current **slew** rates,
* a full protection suite: OVP / OCP / OPP plus under-voltage / under-current,
* channel addressing (``CHANnel <n>``, 1-16) for multi-unit / parallel systems,
* device-reported ranges via SCPI ``MIN`` / ``MAX`` queries.

Because the unit is bidirectional, measured current and power are *signed*:
positive while sourcing, negative while sinking.

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
from dataclasses import dataclass
from typing import Optional

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


@dataclass(frozen=True)
class Measurement:
    """A measured snapshot of the output.

    For this bidirectional supply ``current`` and ``power`` are signed:
    positive while sourcing, negative while sinking.
    """

    voltage: float  # Volts
    current: float  # Amps (negative = sinking)
    power: float    # Watts (negative = sinking)

    def __str__(self) -> str:
        return f"{self.voltage:.4f} V, {self.current:.4f} A, {self.power:.4f} W"


class Channel:
    """A per-channel view of the supply for multi-channel / multi-unit systems.

    Obtained from :meth:`ITN6332B.channel`. Every channel-specific operation
    transparently selects this channel (``CHANnel <n>``) before issuing its
    command, so you can drive several channels without juggling selection::

        psu.channel(1).apply(12.0, 5.0)
        psu.channel(2).apply(5.0, 1.0)
        psu.channel(1).output_on()
        print(psu.channel(2).measure())

    All the channel-scoped methods of :class:`ITN6332B` (voltage/current/power
    setpoints, ``apply``, priority, slew, output control, the protection suite
    and measurements) are available here and behave identically, but pinned to
    this channel.
    """

    # Channel-scoped members of ITN6332B that this proxy selects-then-delegates.
    _DELEGATED = frozenset({
        "set_voltage", "get_voltage", "voltage_range", "set_voltage_limits", "set_voltage_slew",
        "set_current", "get_current", "current_range", "set_current_slew",
        "set_power", "get_power", "apply",
        "set_priority", "get_priority", "set_function_mode",
        "output_on", "output_off", "set_output", "set_output_delays",
        "set_ovp", "set_uvp", "set_ocp", "set_ucp", "set_opp", "clear_protection",
        "questionable_condition", "protection_tripped", "raise_if_tripped",
        "measure_voltage", "measure_current", "measure_power", "measure", "regulation_mode",
    })

    def __init__(self, psu: "ITN6332B", number: int):
        object.__setattr__(self, "_psu", psu)
        object.__setattr__(self, "number", number)

    def select(self) -> None:
        """Make this the active channel on the instrument."""
        self._psu.select_channel(self.number)

    @property
    def available(self) -> bool:
        """Whether the instrument for this channel is present/available."""
        return self._psu.channel_available(self.number)

    @property
    def output_enabled(self) -> bool:
        """Whether this channel's output is enabled."""
        self.select()
        return self._psu.output_enabled

    def __getattr__(self, name: str):
        # Only invoked for names not found normally. Delegate channel-scoped
        # methods to the driver, selecting this channel first.
        if name in Channel._DELEGATED:
            psu = object.__getattribute__(self, "_psu")
            number = object.__getattribute__(self, "number")
            method = getattr(psu, name)

            def bound(*args, **kwargs):
                psu.select_channel(number)
                return method(*args, **kwargs)

            return bound
        raise AttributeError(name)

    def __repr__(self) -> str:
        return f"Channel({self.number})"


class ITN6332B:
    """Driver for an ITECH IT-N6332B bidirectional DC power supply.

    Open with :meth:`open_tcp`, :meth:`open_visa` or :meth:`open_usb`. Commands
    act on the currently selected channel (default 1). For multi-channel /
    multi-unit (paralleled) systems, use :meth:`channel` to get a
    :class:`Channel` proxy per channel, or :meth:`select_channel` to switch the
    active channel for the driver's own methods.
    """

    MAX_CHANNELS = 16  # CHANnel <n> accepts 1..16 per the programming guide

    def __init__(self, connection: ScpiConnection, *, channel: int = 1, claim_remote: bool = True):
        self.scpi = connection
        self._channel: Optional[int] = None
        self._channel_cache: dict[int, "Channel"] = {}
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
        return cls(ScpiConnection.from_visa(resource, timeout=timeout),
                   channel=channel, claim_remote=claim_remote)

    # ------------------------------------------------------------------ #
    # Channel selection (CHANnel <n>, 1-16)
    # ------------------------------------------------------------------ #

    def select_channel(self, number: int) -> None:
        """Select the active channel (1-16). No-op if already selected."""
        if not 1 <= number <= 16:
            raise ValueError("channel must be in 1..16")
        if self._channel != number:
            self.scpi.write(f"CHANnel {number}")
            self._channel = number

    @property
    def selected_channel(self) -> int:
        """The currently selected channel number (see :meth:`select_channel`)."""
        return self._channel if self._channel is not None else 1

    def channel_available(self, number: int) -> bool:
        """Return whether the instrument for the given channel is present."""
        return self.scpi.query(f"CHANnel:STATe? {number}").strip() in ("1", "ON")

    def channel(self, number: int) -> "Channel":
        """Return a :class:`Channel` proxy for the given channel number (1-16)."""
        if not 1 <= number <= self.MAX_CHANNELS:
            raise ValueError(f"channel must be in 1..{self.MAX_CHANNELS}")
        if number not in self._channel_cache:
            self._channel_cache[number] = Channel(self, number)
        return self._channel_cache[number]

    def available_channels(self, max_channels: Optional[int] = None) -> list[int]:
        """Discover which channels are present (via ``CHANnel:STATe?``).

        Probes channels ``1..max_channels`` and returns those the instrument
        reports as available. Channels that raise or report unavailable are
        skipped. Always returns at least ``[1]`` so single-output units work
        even if they don't implement the availability query.
        """
        limit = max_channels or self.MAX_CHANNELS
        found: list[int] = []
        for n in range(1, limit + 1):
            try:
                if self.channel_available(n):
                    found.append(n)
            except Exception:
                break  # instrument rejected the query for this channel number
        return found or [1]

    def channels(self, max_channels: Optional[int] = None) -> list["Channel"]:
        """Return :class:`Channel` proxies for all available channels."""
        return [self.channel(n) for n in self.available_channels(max_channels)]

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

    def set_priority(self, priority: Priority) -> None:
        """Select constant-voltage or constant-current priority."""
        self.scpi.write(f"SOURce:FUNCtion:PRIority {priority.value}")

    def get_priority(self) -> Priority:
        """Return the active priority mode."""
        resp = self.scpi.query("SOURce:FUNCtion:PRIority?").strip().upper()
        return Priority.CURRENT if resp.startswith("CURR") else Priority.VOLTAGE

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

    def set_voltage_limits(self, high: Optional[float] = None, low: Optional[float] = None) -> None:
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

    def set_output_delays(self, on_s: Optional[float] = None, off_s: Optional[float] = None) -> None:
        """Set output on/off sequencing delays in seconds."""
        if on_s is not None:
            self.scpi.write(f"OUTPut:DELay:ON {on_s}")
        if off_s is not None:
            self.scpi.write(f"OUTPut:DELay:OFF {off_s}")

    # ------------------------------------------------------------------ #
    # Protections
    # ------------------------------------------------------------------ #

    def set_ovp(self, volts: float, *, enable: bool = True, delay_s: Optional[float] = None) -> None:
        """Configure over-voltage protection (V)."""
        self.scpi.write(f"SOURce:VOLTage:PROTection:LEVel {volts}")
        if delay_s is not None:
            self.scpi.write(f"SOURce:VOLTage:PROTection:DELay {delay_s}")
        self.scpi.write(f"SOURce:VOLTage:PROTection:STATe {'ON' if enable else 'OFF'}")

    def set_uvp(self, volts: float, *, enable: bool = True) -> None:
        """Configure under-voltage protection (V)."""
        self.scpi.write(f"SOURce:VOLTage:UNDer:PROTection:LEVel {volts}")
        self.scpi.write(f"SOURce:VOLTage:UNDer:PROTection:STATe {'ON' if enable else 'OFF'}")

    def set_ocp(self, amps: float, *, enable: bool = True, delay_s: Optional[float] = None) -> None:
        """Configure over-current protection (A)."""
        self.scpi.write(f"SOURce:CURRent:OVER:PROTection:LEVel {amps}")
        if delay_s is not None:
            self.scpi.write(f"SOURce:CURRent:OVER:PROTection:DELay {delay_s}")
        self.scpi.write(f"SOURce:CURRent:OVER:PROTection:STATe {'ON' if enable else 'OFF'}")

    def set_ucp(self, amps: float, *, enable: bool = True) -> None:
        """Configure under-current protection (A)."""
        self.scpi.write(f"SOURce:CURRent:UNDer:PROTection:LEVel {amps}")
        self.scpi.write(f"SOURce:CURRent:UNDer:PROTection:STATe {'ON' if enable else 'OFF'}")

    def set_opp(self, watts: float, *, enable: bool = True, delay_s: Optional[float] = None) -> None:
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

    def regulation_mode(self) -> str:
        """Best-effort CV/CC indication from the live measurement.

        Compares measured current against the programmed setpoint: at/above it
        means the supply has dropped into constant-current.
        """
        i = abs(self.measure_current())
        limit = abs(self.get_current())
        return "CC" if limit > 0 and i >= limit * 0.98 else "CV"

    # ------------------------------------------------------------------ #
    # Saved states
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Multi-channel convenience
    # ------------------------------------------------------------------ #

    def all_output_on(self, channels: Optional[list[int]] = None) -> None:
        """Enable the output on the given channels (default: all available)."""
        for n in channels or self.available_channels():
            self.channel(n).output_on()

    def all_output_off(self, channels: Optional[list[int]] = None) -> None:
        """Disable the output on the given channels (default: all available)."""
        for n in channels or self.available_channels():
            self.channel(n).output_off()

    def measure_all(self, channels: Optional[list[int]] = None) -> dict[int, Measurement]:
        """Return ``{channel_number: Measurement}`` for the given/all channels."""
        return {n: self.channel(n).measure() for n in (channels or self.available_channels())}

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

    def ramp_voltage(self, target: float, *, step: float = 0.5, interval_s: float = 0.1) -> None:
        """Software-ramp the voltage setpoint to ``target`` in software steps."""
        if step <= 0:
            raise ValueError("step must be positive")
        start = self.get_voltage()
        n = max(1, int(abs(target - start) / step))
        for i in range(1, n + 1):
            self.set_voltage(start + (target - start) * i / n)
            time.sleep(interval_s)
        self.set_voltage(target)

    def shutdown(self) -> None:
        """Safely turn every available channel's output off, then go local."""
        try:
            self.all_output_off()
        except Exception:
            try:
                self.output_off()
            except Exception:
                pass
        finally:
            self.local()

    def close(self) -> None:
        """Close the underlying connection (does not change output state)."""
        self.scpi.close()

    def __enter__(self) -> "ITN6332B":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.shutdown()
        except Exception:
            pass
        self.close()

    def __repr__(self) -> str:
        return f"ITN6332B(ch={self.selected_channel}, {self.scpi!r})"
