"""Driver for the Aim-TTi CPX200DP dual-channel programmable DC power supply.

The CPX200DP is a two-output bench PSU that uses Aim-TTi's proprietary ASCII
command set over LAN (TCP port 9221), USB or RS-232.  Unlike the ITECH command
set, channels are addressed via a numeric **suffix** embedded in every command
(``VSET1``/``VSET2``, ``ISET1``/``ISET2``, etc.) rather than a stateful
channel-select command.

Typical usage::

    from psu_control import CPX200DP

    with CPX200DP.open_tcp("192.168.1.72") as psu:
        print(psu.idn())
        psu.channel(1).apply(12.0, 2.0)
        psu.channel(2).apply(5.0, 1.0)
        psu.all_output_on()
        for n, m in psu.measure_all().items():
            print(f"CH{n}: {m}")

The raw connection is always available via ``psu.scpi.write`` / ``psu.scpi.query``.
"""

from __future__ import annotations

from typing import Optional

from .base import BasePSUDriver, Measurement
from .scpi import DEFAULT_TIMEOUT_S, ScpiConnection

# Aim-TTi LAN SCPI port (CPX / QPX series default).
CPX_TCP_PORT = 9221

# Per-channel electrical limits for the CPX200DP.
_V_MAX = 60.0
_I_MAX = 3.5


class CPX200DP(BasePSUDriver):
    """Driver for an Aim-TTi CPX200DP dual-output programmable power supply.

    Open with :meth:`open_tcp` or :meth:`open_visa`. The two outputs are
    accessed as channels 1 and 2 via the :meth:`~BasePSUDriver.channel` proxy::

        psu.channel(1).apply(12.0, 2.0)
        psu.channel(2).apply(5.0, 1.0)

    Channel commands embed the output number directly in the SCPI mnemonic
    (``VSET1``, ``VSET2``, etc.) so no separate channel-select write is needed.
    """

    MAX_CHANNELS = 2
    DEFAULT_TCP_PORT = CPX_TCP_PORT

    # Hard-coded electrical limits (no MIN/MAX range query on this instrument).
    VOLTAGE_MAX: float = _V_MAX
    CURRENT_MAX: float = _I_MAX

    def __init__(self, connection: ScpiConnection) -> None:
        super().__init__(connection)
        self._channel = 1
        try:
            self.scpi.write("REMOTE")
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Constructors
    # ------------------------------------------------------------------ #

    @classmethod
    def open_tcp(
        cls,
        host: str,
        port: int = CPX_TCP_PORT,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> "CPX200DP":
        """Open a raw TCP socket connection (Aim-TTi default port 9221)."""
        return cls(ScpiConnection.from_tcp(host, port, timeout=timeout))

    @classmethod
    def open_visa(
        cls,
        resource: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        visa_library: str = "@py",
    ) -> "CPX200DP":
        """Open through any PyVISA resource string (TCPIP/USB/serial)."""
        return cls(
            ScpiConnection.from_visa(resource, timeout=timeout, visa_library=visa_library)
        )

    # ------------------------------------------------------------------ #
    # Channel selection (suffix-addressed — no SCPI write needed)
    # ------------------------------------------------------------------ #

    def select_channel(self, number: int) -> None:
        """Record the active channel; CPX commands embed it as a suffix."""
        if not 1 <= number <= self.MAX_CHANNELS:
            raise ValueError(f"channel must be in 1..{self.MAX_CHANNELS}")
        self._channel = number

    def channel_available(self, number: int) -> bool:
        return 1 <= number <= self.MAX_CHANNELS

    def available_channels(self, max_channels: Optional[int] = None) -> list[int]:
        """The CPX200DP always has exactly two outputs."""
        return [1, 2]

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _n(self) -> int:
        """Return the currently selected channel number (1 or 2)."""
        return self._channel if self._channel is not None else 1

    # ------------------------------------------------------------------ #
    # Identification & housekeeping
    # ------------------------------------------------------------------ #

    def idn(self) -> str:
        """Return the ``*IDN?`` identification string."""
        return self.scpi.query("*IDN?")

    def reset(self) -> None:
        """Reset the instrument to its power-on defaults (``*RST``)."""
        self.scpi.write("*RST")
        self._channel = 1

    def clear_status(self) -> None:
        self.scpi.write("*CLS")

    def remote(self) -> None:
        """Enter remote control mode (``REMOTE``)."""
        self.scpi.write("REMOTE")

    def local(self) -> None:
        """Return to local (front-panel) control (``LOCAL``)."""
        self.scpi.write("LOCAL")

    # ------------------------------------------------------------------ #
    # Voltage
    # ------------------------------------------------------------------ #

    def set_voltage(self, volts: float) -> None:
        """Set the voltage setpoint for the active channel (``VSET<n> <v>``)."""
        self.scpi.write(f"VSET{self._n()} {volts:.3f}")

    def get_voltage(self) -> float:
        """Query the voltage setpoint for the active channel (``VSET<n>?``)."""
        return float(self.scpi.query(f"VSET{self._n()}?"))

    def voltage_range(self) -> tuple[float, float]:
        """Return the (min, max) programmable voltage (hard-coded for CPX200DP)."""
        return (0.0, self.VOLTAGE_MAX)

    # ------------------------------------------------------------------ #
    # Current
    # ------------------------------------------------------------------ #

    def set_current(self, amps: float) -> None:
        """Set the current limit for the active channel (``ISET<n> <a>``)."""
        self.scpi.write(f"ISET{self._n()} {amps:.3f}")

    def get_current(self) -> float:
        """Query the current limit for the active channel (``ISET<n>?``)."""
        return float(self.scpi.query(f"ISET{self._n()}?"))

    def current_range(self) -> tuple[float, float]:
        """Return the (min, max) programmable current (hard-coded for CPX200DP).

        The CPX200DP is source-only; minimum is 0 (not negative).
        """
        return (0.0, self.CURRENT_MAX)

    # ------------------------------------------------------------------ #
    # Combined setpoint
    # ------------------------------------------------------------------ #

    def apply(self, voltage: float, current: float) -> None:
        """Set voltage and current for the active channel."""
        self.set_voltage(voltage)
        self.set_current(current)

    # ------------------------------------------------------------------ #
    # Output control
    # ------------------------------------------------------------------ #

    def output_on(self) -> None:
        """Enable the active channel's output (``OUTPUT<n> 1``)."""
        self.scpi.write(f"OUTPUT{self._n()} 1")

    def output_off(self) -> None:
        """Disable the active channel's output (``OUTPUT<n> 0``)."""
        self.scpi.write(f"OUTPUT{self._n()} 0")

    @property
    def output_enabled(self) -> bool:
        """Whether the active channel's output is currently enabled."""
        return self.scpi.query(f"OUTPUT{self._n()}?").strip() in ("1", "ON")

    # ------------------------------------------------------------------ #
    # Protection
    # ------------------------------------------------------------------ #

    def set_ovp(self, volts: float, **kwargs) -> None:
        """Set over-voltage protection for the active channel (``OVP<n> <v>``)."""
        self.scpi.write(f"OVP{self._n()} {volts:.3f}")

    def set_ocp(self, amps: float, **kwargs) -> None:
        """Set over-current protection for the active channel (``OCP<n> <a>``)."""
        self.scpi.write(f"OCP{self._n()} {amps:.3f}")

    def clear_protection(self) -> None:
        """Reset all trip conditions (``TRIPRST``)."""
        self.scpi.write("TRIPRST")

    def protection_tripped(self) -> bool:
        """Whether the active channel has a tripped protection condition.

        Reads ``LSR<n>?`` (Limit Status Register); any non-zero value means
        a protection or regulation event has occurred.
        """
        try:
            return int(self.scpi.query(f"LSR{self._n()}?").strip()) != 0
        except (ValueError, Exception):
            return False

    # ------------------------------------------------------------------ #
    # Measurements
    # ------------------------------------------------------------------ #

    def measure_voltage(self) -> float:
        """Measure the actual output voltage on the active channel (``VOUT<n>?``)."""
        return float(self.scpi.query(f"VOUT{self._n()}?"))

    def measure_current(self) -> float:
        """Measure the actual output current on the active channel (``IOUT<n>?``)."""
        return float(self.scpi.query(f"IOUT{self._n()}?"))

    def measure(self) -> Measurement:
        """Return a :class:`Measurement` snapshot for the active channel."""
        v = self.measure_voltage()
        i = self.measure_current()
        return Measurement(voltage=v, current=i, power=v * i)

    def __repr__(self) -> str:
        return f"CPX200DP(ch={self.selected_channel}, {self.scpi!r})"
