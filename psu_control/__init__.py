"""Python control library for the ITECH IT-N6332B bidirectional DC power supply.

The IT-N6332B uses ITECH's unified SCPI command set (documented in the
IT-M3100 / IT-N6300 Programming Guide, included in ``docs/``). It is a
bidirectional supply -- it can both source and sink power -- with CV/CC priority
selection, positive/negative slew control, a full OVP/OCP/OPP + under-voltage/
under-current protection suite, channel addressing for multi-unit systems, and
device-reported ranges.

Typical usage::

    from psu_control import ITN6332B, Priority

    with ITN6332B.open_tcp("192.168.1.50") as psu:
        print(psu.idn())
        psu.reset()
        psu.set_priority(Priority.VOLTAGE)
        psu.apply(12.0, 5.0)
        psu.output_on()
        print(psu.measure())

See ``examples/`` for more complete programs.
"""

from .exceptions import (
    PSUError,
    PSUConnectionError,
    PSUCommandError,
    PSUProtectionTripped,
    PSUTimeoutError,
)
from .scpi import ScpiConnection
from .it_n6332b import (
    ITN6332B,
    Channel,
    Priority,
    FunctionMode,
    Measurement,
)

__all__ = [
    "ITN6332B",
    "Channel",
    "Priority",
    "FunctionMode",
    "Measurement",
    "ScpiConnection",
    "PSUError",
    "PSUConnectionError",
    "PSUCommandError",
    "PSUProtectionTripped",
    "PSUTimeoutError",
]

__version__ = "3.0.0"
