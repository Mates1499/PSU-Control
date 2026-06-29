"""Python control library for programmable DC power supplies over SCPI.

Supported instruments
---------------------
:class:`ITN6332B`   -- ITECH IT-N6332B bidirectional supply (IT-M3100/IT-N6300 SCPI)
:class:`CPX200DP`   -- Aim-TTi CPX200DP dual-output bench supply

The :class:`~psu_control.base.BasePSUDriver` abstract base class defines the
shared interface; add new instruments by subclassing it.

Typical usage::

    from psu_control import ITN6332B, Priority

    with ITN6332B.open_tcp("192.168.1.50") as psu:
        psu.set_priority(Priority.VOLTAGE)
        psu.apply(12.0, 5.0)
        psu.output_on()
        print(psu.measure())

    from psu_control import CPX200DP

    with CPX200DP.open_tcp("192.168.1.72") as psu:
        psu.channel(1).apply(12.0, 2.0)
        psu.channel(2).apply(5.0, 1.0)
        psu.all_output_on()
        for n, m in psu.measure_all().items():
            print(f"CH{n}: {m}")

See ``examples/`` for more complete programs.
"""

from .base import BasePSUDriver, Channel, Measurement
from .exceptions import (
    PSUError,
    PSUConnectionError,
    PSUCommandError,
    PSUProtectionTripped,
    PSUTimeoutError,
)
from .scpi import ScpiConnection
from .it_n6332b import ITN6332B, Priority, FunctionMode
from .cpx200dp import CPX200DP

__all__ = [
    # Base / shared
    "BasePSUDriver",
    "Channel",
    "Measurement",
    # Drivers
    "ITN6332B",
    "CPX200DP",
    # IT-N6332B enums
    "Priority",
    "FunctionMode",
    # Transport
    "ScpiConnection",
    # Exceptions
    "PSUError",
    "PSUConnectionError",
    "PSUCommandError",
    "PSUProtectionTripped",
    "PSUTimeoutError",
]

__version__ = "4.0.0"
