"""Abstract base driver and shared data types for all supported PSU models.

Concrete drivers live in sub-modules and subclass :class:`BasePSUDriver`:

* ``it_n6332b.py``  -- ITECH IT-N6332B (bidirectional, SCPI CHANnel addressing)
* ``cpx200dp.py``   -- Aim-TTi CPX200DP (source-only, suffix-addressed commands)

Add future models by subclassing :class:`BasePSUDriver` and implementing the
abstract methods; the :class:`Channel` proxy, CLI and web UI automatically work
with any conforming driver.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Optional

from .scpi import ScpiConnection


# -------------------------------------------------------------------------- #
# Shared data types
# -------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Measurement:
    """A measured snapshot of one PSU output.

    For bidirectional supplies (e.g. IT-N6332B) ``current`` and ``power`` are
    signed: positive while sourcing, negative while sinking.
    """

    voltage: float   # Volts
    current: float   # Amps  (negative = sinking for bidirectional units)
    power: float     # Watts (negative = sinking for bidirectional units)

    def __str__(self) -> str:
        return f"{self.voltage:.4f} V, {self.current:.4f} A, {self.power:.4f} W"


# -------------------------------------------------------------------------- #
# Generic channel proxy
# -------------------------------------------------------------------------- #


class Channel:
    """Generic per-channel proxy for any :class:`BasePSUDriver`.

    Obtained via ``driver.channel(n)``. Every delegated call transparently
    selects this channel (``driver.select_channel(n)``) before issuing its
    command, so you can drive multiple outputs without manual selection::

        psu.channel(1).apply(12.0, 5.0)
        psu.channel(2).apply(5.0, 1.0)
        psu.channel(1).output_on()
        print(psu.channel(2).measure())

    All channel-scoped methods of the underlying driver are delegated here.
    """

    _DELEGATED: frozenset[str] = frozenset({
        # Voltage
        "set_voltage", "get_voltage", "voltage_range", "set_voltage_limits", "set_voltage_slew",
        # Current
        "set_current", "get_current", "current_range", "set_current_slew",
        # Power
        "set_power", "get_power",
        # Combined
        "apply",
        # Priority / mode
        "set_priority", "get_priority", "set_function_mode",
        # Output
        "output_on", "output_off", "set_output", "set_output_delays",
        # Protection
        "set_ovp", "set_uvp", "set_ocp", "set_ucp", "set_opp", "clear_protection",
        "questionable_condition", "protection_tripped", "raise_if_tripped",
        # Measurements
        "measure_voltage", "measure_current", "measure_power", "measure", "regulation_mode",
    })

    def __init__(self, psu: "BasePSUDriver", number: int) -> None:
        object.__setattr__(self, "_psu", psu)
        object.__setattr__(self, "number", number)

    def select(self) -> None:
        """Make this the active channel on the instrument."""
        self._psu.select_channel(self.number)

    @property
    def available(self) -> bool:
        """Whether the instrument reports this channel as present."""
        return self._psu.channel_available(self.number)

    @property
    def output_enabled(self) -> bool:
        """Whether this channel's output is currently enabled."""
        self.select()
        return self._psu.output_enabled

    def __getattr__(self, name: str):
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


# -------------------------------------------------------------------------- #
# Abstract base driver
# -------------------------------------------------------------------------- #


class BasePSUDriver(abc.ABC):
    """Abstract interface for a programmable DC power supply.

    Subclasses implement the SCPI (or proprietary) dialect for a specific
    instrument. The :class:`Channel` proxy, CLI and web UI all program against
    this interface, so they work unchanged with any supported model.

    Two channel-addressing styles are accommodated:

    * **Stateful select** (e.g. ITECH ``CHANnel <n>``): override
      :meth:`select_channel` to write the selection command and cache it to
      avoid redundant writes.  All subsequent bare commands act on the
      selected channel.
    * **Suffix-addressed** (e.g. Aim-TTi ``VSET1``/``VSET2``): the default
      :meth:`select_channel` just records ``number``; driver methods embed it
      as a suffix when building SCPI strings.
    """

    MAX_CHANNELS: int = 1
    DEFAULT_TCP_PORT: int = 5025  # generic SCPI port; override per model

    def __init__(self, connection: ScpiConnection) -> None:
        self.scpi = connection
        self._channel: Optional[int] = None
        self._channel_cache: dict[int, Channel] = {}

    # ------------------------------------------------------------------ #
    # Constructors  (concrete models add open_visa / open_usb as needed)
    # ------------------------------------------------------------------ #

    @classmethod
    def open_tcp(
        cls,
        host: str,
        port: int = 0,
        *,
        timeout: float = 5.0,
        **kwargs,
    ) -> "BasePSUDriver":
        """Open a raw TCP socket connection."""
        from .scpi import ScpiConnection
        p = port or cls.DEFAULT_TCP_PORT
        return cls(ScpiConnection.from_tcp(host, p, timeout=timeout), **kwargs)

    @classmethod
    def open_visa(
        cls,
        resource: str,
        *,
        timeout: float = 5.0,
        **kwargs,
    ) -> "BasePSUDriver":
        """Open through a PyVISA resource string."""
        from .scpi import ScpiConnection
        return cls(ScpiConnection.from_visa(resource, timeout=timeout), **kwargs)

    # ------------------------------------------------------------------ #
    # Channel management
    # ------------------------------------------------------------------ #

    def select_channel(self, number: int) -> None:
        """Make the given channel active.

        The default implementation just records ``number`` in ``_channel``.
        Drivers with suffix-addressed commands (e.g. ``VSET1``) read it back
        via :attr:`selected_channel`.  Drivers with stateful select commands
        (e.g. ITECH ``CHANnel <n>``) should override this to write the
        selection and cache to avoid repeat writes.
        """
        self._channel = number

    @property
    def selected_channel(self) -> int:
        """The currently active channel number."""
        return self._channel if self._channel is not None else 1

    def channel(self, number: int) -> Channel:
        """Return a :class:`Channel` proxy for the given output number."""
        if not 1 <= number <= self.MAX_CHANNELS:
            raise ValueError(f"channel must be in 1..{self.MAX_CHANNELS}")
        if number not in self._channel_cache:
            self._channel_cache[number] = Channel(self, number)
        return self._channel_cache[number]

    def channel_available(self, number: int) -> bool:
        """Return whether the instrument reports this channel as present."""
        return 1 <= number <= self.MAX_CHANNELS

    def available_channels(self, max_channels: Optional[int] = None) -> list[int]:
        """Return a list of available channel numbers.

        Probes :meth:`channel_available` for ``1..max_channels``. Override for
        driver-specific discovery or fixed channel counts.
        """
        limit = max_channels or self.MAX_CHANNELS
        found: list[int] = []
        for n in range(1, limit + 1):
            try:
                if self.channel_available(n):
                    found.append(n)
            except Exception:
                break
        return found or [1]

    def channels(self, max_channels: Optional[int] = None) -> list[Channel]:
        """Return :class:`Channel` proxies for all available channels."""
        return [self.channel(n) for n in self.available_channels(max_channels)]

    # ------------------------------------------------------------------ #
    # Multi-channel convenience
    # ------------------------------------------------------------------ #

    def all_output_on(self, channels: Optional[list[int]] = None) -> None:
        """Enable the output on all (or the given) channels."""
        for n in channels or self.available_channels():
            self.channel(n).output_on()

    def all_output_off(self, channels: Optional[list[int]] = None) -> None:
        """Disable the output on all (or the given) channels."""
        for n in channels or self.available_channels():
            self.channel(n).output_off()

    def measure_all(self, channels: Optional[list[int]] = None) -> dict[int, Measurement]:
        """Return ``{channel_number: Measurement}`` for all (or given) channels."""
        return {n: self.channel(n).measure() for n in (channels or self.available_channels())}

    # ------------------------------------------------------------------ #
    # Identification & housekeeping
    # ------------------------------------------------------------------ #

    @abc.abstractmethod
    def idn(self) -> str:
        """Return the instrument identification string (``*IDN?``)."""

    def reset(self) -> None:
        """Reset the instrument to power-on state (``*RST``)."""
        self.scpi.write("*RST")
        self._channel = None

    def clear_status(self) -> None:
        """Clear status registers and the error queue (``*CLS``)."""
        self.scpi.write("*CLS")

    def check_errors(self) -> None:
        """Drain the error queue; raise on any error.  Default: no-op."""

    def remote(self) -> None:
        """Enter remote control mode (lock front panel).  Default: no-op."""

    def local(self) -> None:
        """Return to local (front-panel) control.  Default: no-op."""

    # ------------------------------------------------------------------ #
    # Voltage
    # ------------------------------------------------------------------ #

    @abc.abstractmethod
    def set_voltage(self, volts: float) -> None:
        """Set the output voltage setpoint (V)."""

    @abc.abstractmethod
    def get_voltage(self) -> float:
        """Return the programmed voltage setpoint (V)."""

    def voltage_range(self) -> tuple[float, float]:
        """Return the (min, max) programmable voltage.  Override with device values."""
        return (0.0, float("inf"))

    def set_voltage_limits(self, high: Optional[float] = None, low: Optional[float] = None) -> None:
        """Set soft voltage limits.  Default: no-op; override if supported."""

    def set_voltage_slew(
        self,
        both: Optional[float] = None,
        *,
        positive: Optional[float] = None,
        negative: Optional[float] = None,
    ) -> None:
        """Set voltage slew rate (V/s).  Default: no-op; override if supported."""

    # ------------------------------------------------------------------ #
    # Current
    # ------------------------------------------------------------------ #

    @abc.abstractmethod
    def set_current(self, amps: float) -> None:
        """Set the current setpoint / limit (A)."""

    @abc.abstractmethod
    def get_current(self) -> float:
        """Return the programmed current setpoint (A)."""

    def current_range(self) -> tuple[float, float]:
        """Return the (min, max) programmable current.  Override with device values."""
        return (0.0, float("inf"))

    def set_current_slew(
        self,
        both: Optional[float] = None,
        *,
        positive: Optional[float] = None,
        negative: Optional[float] = None,
    ) -> None:
        """Set current slew rate (A/s).  Default: no-op; override if supported."""

    # ------------------------------------------------------------------ #
    # Power
    # ------------------------------------------------------------------ #

    def set_power(self, watts: float) -> None:
        """Set the power setpoint (W).  Default: no-op; override if supported."""

    def get_power(self) -> float:
        """Return the programmed power setpoint (W)."""
        return self.measure_voltage() * self.measure_current()

    # ------------------------------------------------------------------ #
    # Combined setpoint
    # ------------------------------------------------------------------ #

    def apply(self, voltage: float, current: float) -> None:
        """Set voltage and current together."""
        self.set_voltage(voltage)
        self.set_current(current)

    # ------------------------------------------------------------------ #
    # Output control
    # ------------------------------------------------------------------ #

    @abc.abstractmethod
    def output_on(self) -> None:
        """Enable the output."""

    @abc.abstractmethod
    def output_off(self) -> None:
        """Disable the output."""

    def set_output(self, enabled: bool) -> None:
        """Enable or disable the output."""
        self.output_on() if enabled else self.output_off()

    def set_output_delays(self, on_s: Optional[float] = None, off_s: Optional[float] = None) -> None:
        """Set output on/off sequencing delays.  Default: no-op."""

    @property
    @abc.abstractmethod
    def output_enabled(self) -> bool:
        """Whether the output is currently enabled."""

    # ------------------------------------------------------------------ #
    # Priority / mode (CV/CC) — optional for source-only supplies
    # ------------------------------------------------------------------ #

    def set_priority(self, priority) -> None:
        """Select CV or CC priority.  Default: no-op (CV-only supplies)."""

    def get_priority(self) -> str:
        """Return the active priority as ``\"VOLTAGE\"`` or ``\"CURRENT\"``."""
        return "VOLTAGE"

    def set_function_mode(self, mode) -> None:
        """Set the operating mode.  Default: no-op."""

    # ------------------------------------------------------------------ #
    # Protection — all no-ops by default; override to support
    # ------------------------------------------------------------------ #

    def set_ovp(self, volts: float, **kwargs) -> None:
        """Configure over-voltage protection.  Default: no-op."""

    def set_uvp(self, volts: float, **kwargs) -> None:
        """Configure under-voltage protection.  Default: no-op."""

    def set_ocp(self, amps: float, **kwargs) -> None:
        """Configure over-current protection.  Default: no-op."""

    def set_ucp(self, amps: float, **kwargs) -> None:
        """Configure under-current protection.  Default: no-op."""

    def set_opp(self, watts: float, **kwargs) -> None:
        """Configure over-power protection.  Default: no-op."""

    def clear_protection(self) -> None:
        """Clear any latched protection fault.  Default: no-op."""

    def questionable_condition(self) -> int:
        """Return the raw questionable-status register.  Default: 0."""
        return 0

    def protection_tripped(self) -> bool:
        """Whether any protection has tripped.  Default: False."""
        return False

    def raise_if_tripped(self) -> None:
        """Raise if any protection has tripped.  Default: no-op."""

    # ------------------------------------------------------------------ #
    # Measurements
    # ------------------------------------------------------------------ #

    @abc.abstractmethod
    def measure_voltage(self) -> float:
        """Measure the actual output voltage (V)."""

    @abc.abstractmethod
    def measure_current(self) -> float:
        """Measure the actual output current (A); negative while sinking."""

    def measure_power(self) -> float:
        """Measure the actual output power (W); computed as V × I."""
        return self.measure_voltage() * self.measure_current()

    def measure(self) -> Measurement:
        """Return a single :class:`Measurement` snapshot of V, I and P."""
        v = self.measure_voltage()
        i = self.measure_current()
        return Measurement(voltage=v, current=i, power=v * i)

    def regulation_mode(self) -> str:
        """Return ``\"CV\"`` or ``\"CC\"`` based on live measurements."""
        i = abs(self.measure_current())
        limit = abs(self.get_current())
        return "CC" if limit > 0 and i >= limit * 0.98 else "CV"

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def shutdown(self) -> None:
        """Turn all outputs off and return to local control (fail-safe)."""
        try:
            self.all_output_off()
        except Exception:
            try:
                self.output_off()
            except Exception:
                pass
        try:
            self.local()
        except Exception:
            pass

    def close(self) -> None:
        """Close the underlying transport connection."""
        self.scpi.close()

    def __enter__(self) -> "BasePSUDriver":
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.shutdown()
        except Exception:
            pass
        self.close()
