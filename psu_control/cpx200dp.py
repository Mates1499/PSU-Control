"""Driver for the Aim-TTi CPX200DP dual-channel programmable DC power supply.

The CPX200DP is a two-output bench PSU that uses Aim-TTi's ASCII remote command
set over LAN (TCP port 9221), USB or RS-232.  Unlike the ITECH command set,
channels are addressed via a numeric **suffix** embedded in every command
rather than a stateful channel-select command.

Command forms (from the CPX200D/DP Instruction Manual, "Remote Commands"):

    V1 12.0        # set output 1 voltage
    I1 2.0         # set output 1 current limit
    V1?            # query setpoint  -> "V1 12.00"
    I1?            # query limit     -> "I1 2.000"
    V1O?           # measure voltage -> "12.000V"
    I1O?           # measure current -> "1.000A"
    OP1 1 / OP1 0  # output 1 on/off
    OP1?           # -> "1" or "0"
    OPALL 1|0      # all outputs on/off simultaneously
    OVP1 13.5      # over-voltage trip point; query -> "VP1 13.50"
    OCP1 2.5       # over-current trip point; query -> "CP1 2.500"
    TRIPRST        # attempt to clear all trip conditions
    LSR1?          # query-and-clear Limit Event Status Register 1

Typical usage::

    from psu_control import CPX200DP

    with CPX200DP.open_tcp("192.168.200.101") as psu:
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
from .exceptions import PSUCommandError
from .scpi import DEFAULT_TIMEOUT_S, ScpiConnection

# Aim-TTi LAN port (CPX / QPX / PL series default).
CPX_TCP_PORT = 9221

# Per-channel electrical limits for the CPX200DP (60 V / 10 A, 180 W per output).
_V_MAX = 60.0
_I_MAX = 10.0

# Limit Event Status Register bits (per manual "Status Reporting"):
#   bit 0 = CV mode          bit 1 = CC mode
#   bit 2 = OVP trip         bit 3 = OCP trip
#   bit 4 = power limit      bit 6 = hard trip (front-panel/power-cycle reset)
_LSR_CV = 0x01
_LSR_CC = 0x02
_LSR_TRIP_MASK = 0x4C   # OVP | OCP | hard trip


class CPX200DP(BasePSUDriver):
    """Driver for an Aim-TTi CPX200DP dual-output programmable power supply.

    Open with :meth:`open_tcp` or :meth:`open_visa`. The two outputs are
    accessed as channels 1 and 2 via the :meth:`~BasePSUDriver.channel` proxy::

        psu.channel(1).apply(12.0, 2.0)
        psu.channel(2).apply(5.0, 1.0)

    Channel commands embed the output number directly in the mnemonic
    (``V1``, ``OP2``, etc.) so no separate channel-select write is needed.
    """

    MAX_CHANNELS = 2
    DEFAULT_TCP_PORT = CPX_TCP_PORT

    # Hard-coded electrical limits (no MIN/MAX range query on this instrument).
    VOLTAGE_MAX: float = _V_MAX
    CURRENT_MAX: float = _I_MAX

    def __init__(self, connection: ScpiConnection) -> None:
        super().__init__(connection)
        self._channel = 1
        # LSR reads clear the register, so latch trip bits per channel to
        # avoid losing a trip between polls.
        self._trip_latch: dict[int, int] = {1: 0, 2: 0}

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

    @staticmethod
    def _parse_reply(reply: str) -> float:
        """Parse a value out of an Aim-TTi response.

        Setpoint queries return a prefixed form (``"V1 12.00"``); readback
        queries return a unit-suffixed form (``"12.000V"``).  Both are handled.
        """
        token = reply.strip().split()[-1] if reply.strip() else ""
        token = token.rstrip("VvAaWw")
        try:
            return float(token)
        except ValueError:
            raise PSUCommandError(-1, f"Unparseable instrument reply: {reply!r}")

    # ------------------------------------------------------------------ #
    # Identification & housekeeping
    # ------------------------------------------------------------------ #

    def idn(self) -> str:
        """Return the ``*IDN?`` identification string."""
        return self.scpi.query("*IDN?")

    def reset(self) -> None:
        """Reset the instrument to its remote-control defaults (``*RST``)."""
        self.scpi.write("*RST")
        self._channel = 1
        self._trip_latch = {1: 0, 2: 0}

    def clear_status(self) -> None:
        self.scpi.write("*CLS")

    def local(self) -> None:
        """Return to local (front-panel) control (``LOCAL``)."""
        self.scpi.write("LOCAL")

    # ``remote()`` stays a no-op: the CPX enters remote automatically on the
    # first command; the manual defines no REMOTE command.

    # ------------------------------------------------------------------ #
    # Voltage
    # ------------------------------------------------------------------ #

    def set_voltage(self, volts: float) -> None:
        """Set the voltage setpoint for the active channel (``V<n> <v>``)."""
        self.scpi.write(f"V{self._n()} {volts:.3f}")

    def get_voltage(self) -> float:
        """Query the voltage setpoint (``V<n>?`` -> ``"V<n> <val>"``)."""
        return self._parse_reply(self.scpi.query(f"V{self._n()}?"))

    def voltage_range(self) -> tuple[float, float]:
        """Return the (min, max) programmable voltage (rated for CPX200DP)."""
        return (0.0, self.VOLTAGE_MAX)

    # ------------------------------------------------------------------ #
    # Current
    # ------------------------------------------------------------------ #

    def set_current(self, amps: float) -> None:
        """Set the current limit for the active channel (``I<n> <a>``)."""
        self.scpi.write(f"I{self._n()} {amps:.3f}")

    def get_current(self) -> float:
        """Query the current limit (``I<n>?`` -> ``"I<n> <val>"``)."""
        return self._parse_reply(self.scpi.query(f"I{self._n()}?"))

    def current_range(self) -> tuple[float, float]:
        """Return the (min, max) programmable current (rated for CPX200DP).

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
        """Enable the active channel's output (``OP<n> 1``)."""
        self.scpi.write(f"OP{self._n()} 1")

    def output_off(self) -> None:
        """Disable the active channel's output (``OP<n> 0``)."""
        self.scpi.write(f"OP{self._n()} 0")

    @property
    def output_enabled(self) -> bool:
        """Whether the active channel's output is currently enabled."""
        return self.scpi.query(f"OP{self._n()}?").strip() == "1"

    def all_output_on(self, channels: Optional[list[int]] = None) -> None:
        """Enable outputs; uses ``OPALL 1`` (simultaneous) when no subset given."""
        if channels:
            super().all_output_on(channels)
        else:
            self.scpi.write("OPALL 1")

    def all_output_off(self, channels: Optional[list[int]] = None) -> None:
        """Disable outputs; uses ``OPALL 0`` (simultaneous) when no subset given."""
        if channels:
            super().all_output_off(channels)
        else:
            self.scpi.write("OPALL 0")

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
        """Reset all trip conditions (``TRIPRST``) and the local trip latch."""
        self.scpi.write("TRIPRST")
        self._trip_latch[self._n()] = 0

    def _read_lsr(self) -> int:
        """Read-and-clear this channel's Limit Event Status Register."""
        try:
            return int(self.scpi.query(f"LSR{self._n()}?").strip())
        except ValueError:
            return 0

    def protection_tripped(self) -> bool:
        """Whether the active channel has a tripped protection condition.

        ``LSR<n>?`` clears on read, so trip bits are latched locally until
        :meth:`clear_protection` is called.
        """
        n = self._n()
        self._trip_latch[n] |= self._read_lsr() & _LSR_TRIP_MASK
        return self._trip_latch[n] != 0

    # ------------------------------------------------------------------ #
    # Measurements
    # ------------------------------------------------------------------ #

    def measure_voltage(self) -> float:
        """Measure the output voltage (``V<n>O?`` -> ``"<val>V"``)."""
        return self._parse_reply(self.scpi.query(f"V{self._n()}O?"))

    def measure_current(self) -> float:
        """Measure the output current (``I<n>O?`` -> ``"<val>A"``)."""
        return self._parse_reply(self.scpi.query(f"I{self._n()}O?"))

    def measure(self) -> Measurement:
        """Return a :class:`Measurement` snapshot for the active channel."""
        v = self.measure_voltage()
        i = self.measure_current()
        return Measurement(voltage=v, current=i, power=v * i)

    def __repr__(self) -> str:
        return f"CPX200DP(ch={self.selected_channel}, {self.scpi!r})"
