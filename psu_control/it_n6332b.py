"""High-level driver for the ITECH IT-N6332B triple-channel DC power supply.

The IT-N6332B belongs to ITECH's IT-N6300 / IT6300 family of **triple-channel**
programmable DC power supplies (model line IT6322/IT6332/IT6333). It is *not* a
bidirectional supply -- it has three independent, isolated source-only output
channels that can also be combined in series/parallel tracking:

================  ==============  =============  ===========
Channel           Voltage         Current        Power
================  ==============  =============  ===========
CH1               0 - 30 V        0 - 6 A        180 W
CH2               0 - 30 V        0 - 6 A        180 W
CH3               0 - 5 V         0 - 3 A        15 W
================  ==============  =============  ===========

Setpoint resolution is 1 mV / 1 mA. The unit is remote-controllable over LAN,
USB-TMC, GPIB and RS-232 using SCPI.

The SCPI model is channel-oriented: you select the active channel with
``INSTrument:NSELect`` / ``INSTrument:SELect`` and then issue per-channel
``VOLTage`` / ``CURRent`` / ``OUTPut`` / ``MEASure`` commands. This driver hides
that handshake behind a :class:`Channel` object, so you write::

    with ITN6332B.open_tcp("192.168.1.50") as psu:
        psu.ch1.apply(12.0, 2.0)     # 12 V, 2 A limit on channel 1
        psu.ch1.output_on()
        print(psu.ch1.measure())     # 12.00 V, 0.50 A, 6.0 W

Command mnemonics follow the IT6300 series Programming Guide (which documents
the IT6322/IT6332/IT6333 A/B/C models). If a firmware revision differs, the raw
connection remains available via ``psu.scpi.write`` / ``psu.scpi.query``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .exceptions import PSUCommandError, PSUProtectionTripped
from .scpi import DEFAULT_SCPI_PORT, DEFAULT_TIMEOUT_S, ScpiConnection


@dataclass(frozen=True)
class ChannelSpec:
    """Static ratings of one output channel, used for range validation."""

    number: int       # 1-based channel index used by INSTrument:NSELect
    name: str         # SCPI channel name, e.g. "CH1"
    max_voltage: float
    max_current: float
    max_power: float


# Factory ratings of the IT-N6332B (see module docstring).
IT_N6332B_CHANNELS = (
    ChannelSpec(1, "CH1", 30.0, 6.0, 180.0),
    ChannelSpec(2, "CH2", 30.0, 6.0, 180.0),
    ChannelSpec(3, "CH3", 5.0, 3.0, 15.0),
)


@dataclass(frozen=True)
class Measurement:
    """A measured snapshot of one channel's output."""

    voltage: float  # Volts
    current: float  # Amps
    power: float    # Watts

    def __str__(self) -> str:
        return f"{self.voltage:.3f} V, {self.current:.3f} A, {self.power:.3f} W"


class Channel:
    """One output channel of the supply.

    Obtained via ``psu.ch1`` / ``psu.ch2`` / ``psu.ch3`` or ``psu.channel(n)``.
    Every method transparently selects this channel on the instrument before
    issuing its command, so callers never deal with ``INSTrument`` directly.
    """

    def __init__(self, psu: "ITN6332B", spec: ChannelSpec):
        self._psu = psu
        self.spec = spec

    # -- selection --------------------------------------------------------

    def select(self) -> None:
        """Make this the active channel for subsequent raw SCPI commands."""
        self._psu._select(self.spec.number)

    # -- setpoints --------------------------------------------------------

    def set_voltage(self, volts: float) -> None:
        """Set this channel's output voltage (V)."""
        self._check("voltage", volts, self.spec.max_voltage)
        self.select()
        self._psu.scpi.write(f"VOLTage {volts}")

    def get_voltage(self) -> float:
        """Return this channel's programmed voltage setpoint (V)."""
        self.select()
        return float(self._psu.scpi.query("VOLTage?"))

    def set_current(self, amps: float) -> None:
        """Set this channel's current limit (A)."""
        self._check("current", amps, self.spec.max_current)
        self.select()
        self._psu.scpi.write(f"CURRent {amps}")

    def get_current(self) -> float:
        """Return this channel's programmed current limit (A)."""
        self.select()
        return float(self._psu.scpi.query("CURRent?"))

    def set_voltage_limit(self, volts: float) -> None:
        """Set the soft maximum-voltage limit for this channel (V)."""
        self.select()
        self._psu.scpi.write(f"VOLTage:LIMit {volts}")

    def apply(self, voltage: float, current: float) -> None:
        """Set voltage and current limit together (``APPLy <v>,<a>``)."""
        self._check("voltage", voltage, self.spec.max_voltage)
        self._check("current", current, self.spec.max_current)
        self.select()
        self._psu.scpi.write(f"APPLy {voltage},{current}")

    # -- output -----------------------------------------------------------

    def output_on(self) -> None:
        """Enable this channel's output."""
        self.select()
        self._psu.scpi.write("OUTPut:STATe ON")

    def output_off(self) -> None:
        """Disable this channel's output."""
        self.select()
        self._psu.scpi.write("OUTPut:STATe OFF")

    def set_output(self, enabled: bool) -> None:
        self.select()
        self._psu.scpi.write(f"OUTPut:STATe {'ON' if enabled else 'OFF'}")

    @property
    def output_enabled(self) -> bool:
        """Whether this channel's output is currently enabled."""
        self.select()
        return self._psu.scpi.query("OUTPut:STATe?").strip() in ("1", "ON")

    # -- protection -------------------------------------------------------

    def set_ovp(self, volts: float, *, enable: bool = True) -> None:
        """Configure over-voltage protection (OVP) for this channel (V)."""
        self.select()
        self._psu.scpi.write(f"VOLTage:PROTection:LEVel {volts}")
        self._psu.scpi.write(
            f"VOLTage:PROTection:STATe {'ON' if enable else 'OFF'}"
        )

    # -- measurements -----------------------------------------------------

    def measure_voltage(self) -> float:
        """Measure the actual output voltage of this channel (V)."""
        self.select()
        return float(self._psu.scpi.query("MEASure:VOLTage:DC?"))

    def measure_current(self) -> float:
        """Measure the actual output current of this channel (A)."""
        self.select()
        return float(self._psu.scpi.query("MEASure:CURRent:DC?"))

    def measure_power(self) -> float:
        """Measure the actual output power of this channel (W)."""
        self.select()
        return float(self._psu.scpi.query("MEASure:POWer:DC?"))

    def measure(self) -> Measurement:
        """Return a :class:`Measurement` snapshot (V, I, P) for this channel."""
        return Measurement(
            voltage=self.measure_voltage(),
            current=self.measure_current(),
            power=self.measure_power(),
        )

    def regulation_mode(self) -> str:
        """Best-effort CV/CC indication from the live measurement.

        The IT6300 family does not expose a simple per-channel CV/CC query, so
        this compares measured current against the programmed limit: at/above
        the limit means the channel has dropped into constant-current.
        """
        i = self.measure_current()
        limit = self.get_current()
        return "CC" if limit > 0 and i >= limit * 0.98 else "CV"

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _check(kind: str, value: float, maximum: float) -> None:
        if value < 0 or value > maximum:
            raise ValueError(
                f"{kind} {value} out of range for this channel (0..{maximum})"
            )

    def __repr__(self) -> str:
        return f"Channel({self.spec.name}, {self.spec.max_voltage}V/{self.spec.max_current}A)"


class ITN6332B:
    """Driver for an ITECH IT-N6332B triple-channel power supply.

    Open via :meth:`open_tcp`, :meth:`open_visa` or :meth:`open_usb`, then use
    the per-channel objects :attr:`ch1`, :attr:`ch2`, :attr:`ch3`::

        with ITN6332B.open_tcp("192.168.1.50") as psu:
            psu.ch1.apply(12.0, 2.0)
            psu.ch1.output_on()
    """

    #: Tracking modes for combining CH1 + CH2 (``set_tracking``).
    TRACK_OFF = "OFF"
    TRACK_SERIES = "SERies"
    TRACK_PARALLEL = "PARallel"

    def __init__(self, connection: ScpiConnection, *, claim_remote: bool = True):
        self.scpi = connection
        self._selected: Optional[int] = None
        self.channels = tuple(Channel(self, spec) for spec in IT_N6332B_CHANNELS)
        if claim_remote:
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
        product_id: Optional[int] = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        claim_remote: bool = True,
    ) -> "ITN6332B":
        """Open over USB-TMC via PyVISA.

        If ``serial``/``product_id`` are omitted, wildcards are used and the
        first matching ITECH instrument is opened.
        """
        pid = f"0x{product_id:04X}" if product_id is not None else "?*"
        serial_part = serial if serial is not None else "?*"
        resource = f"USB::0x{vendor_id:04X}::{pid}::{serial_part}::INSTR"
        return cls(
            ScpiConnection.from_visa(resource, timeout=timeout),
            claim_remote=claim_remote,
        )

    # ------------------------------------------------------------------ #
    # Channel access
    # ------------------------------------------------------------------ #

    @property
    def ch1(self) -> Channel:
        return self.channels[0]

    @property
    def ch2(self) -> Channel:
        return self.channels[1]

    @property
    def ch3(self) -> Channel:
        return self.channels[2]

    def channel(self, number: int) -> Channel:
        """Return the channel with the given 1-based number."""
        for ch in self.channels:
            if ch.spec.number == number:
                return ch
        raise ValueError(f"No such channel: {number}")

    def _select(self, number: int) -> None:
        """Select the active channel, skipping the write if already selected."""
        if self._selected != number:
            self.scpi.write(f"INSTrument:NSELect {number}")
            self._selected = number

    # ------------------------------------------------------------------ #
    # Identification & housekeeping
    # ------------------------------------------------------------------ #

    def idn(self) -> str:
        """Return the ``*IDN?`` identification string."""
        return self.scpi.query("*IDN?")

    def reset(self) -> None:
        """Reset the instrument to its power-on configuration (``*RST``)."""
        self.scpi.write("*RST")
        self._selected = None
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
        self.scpi.write("SYSTem:BEEPer")

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
            first = (code, msg)
            # Drain the rest so the queue is clean for next time.
            while True:
                nxt_code, _ = self.next_error()
                if nxt_code == 0:
                    break
            raise PSUCommandError(first[0], first[1])

    # ------------------------------------------------------------------ #
    # Multi-channel convenience
    # ------------------------------------------------------------------ #

    def all_output_on(self) -> None:
        """Enable the output on every channel."""
        for ch in self.channels:
            ch.output_on()

    def all_output_off(self) -> None:
        """Disable the output on every channel."""
        for ch in self.channels:
            ch.output_off()

    def measure_all(self) -> dict[str, Measurement]:
        """Return ``{channel_name: Measurement}`` for all channels."""
        return {ch.spec.name: ch.measure() for ch in self.channels}

    # ------------------------------------------------------------------ #
    # Series / parallel tracking (combines CH1 + CH2)
    # ------------------------------------------------------------------ #

    def set_tracking(self, mode: str) -> None:
        """Set CH1+CH2 tracking mode.

        Args:
            mode: One of :attr:`TRACK_OFF`, :attr:`TRACK_SERIES`,
                :attr:`TRACK_PARALLEL`.
        """
        self.scpi.write(f"OUTPut:TRACk {mode}")

    # ------------------------------------------------------------------ #
    # Saved states
    # ------------------------------------------------------------------ #

    def save_state(self, slot: int) -> None:
        """Save the current setup to non-volatile memory ``slot`` (``*SAV``)."""
        self.scpi.write(f"*SAV {int(slot)}")

    def recall_state(self, slot: int) -> None:
        """Recall a saved setup from memory ``slot`` (``*RCL``)."""
        self.scpi.write(f"*RCL {int(slot)}")
        self._selected = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def shutdown(self) -> None:
        """Safely turn every output off and return to local control."""
        try:
            self.all_output_off()
        finally:
            self.local()

    def close(self) -> None:
        """Close the underlying connection (does not change output state)."""
        self.scpi.close()

    def __enter__(self) -> "ITN6332B":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Fail safe: turn all outputs off, release remote, then close.
        try:
            self.shutdown()
        except Exception:
            pass
        self.close()

    def __repr__(self) -> str:
        return f"ITN6332B({self.scpi!r})"
