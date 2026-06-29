"""Python control library for the ITECH IT-N6332B bidirectional DC power supply.

The IT-N6332B belongs to ITECH's IT-N6300 series of programmable bidirectional
(two-quadrant) DC power supplies. It can both *source* power (behave as a power
supply) and *sink* power (behave as an electronic load), and is fully
remote-controllable over LAN / USB / GPIB using SCPI commands.

Typical usage::

    from psu_control import ITN6332B

    with ITN6332B.open_tcp("192.168.1.50") as psu:
        print(psu.idn())
        psu.reset()
        psu.set_voltage(12.0)
        psu.set_current_limit(2.0)
        psu.output_on()
        print(psu.measure_voltage(), "V")
        print(psu.measure_current(), "A")

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
    OutputMode,
    ProtectionStatus,
    Measurement,
)

__all__ = [
    "ITN6332B",
    "OutputMode",
    "ProtectionStatus",
    "Measurement",
    "ScpiConnection",
    "PSUError",
    "PSUConnectionError",
    "PSUCommandError",
    "PSUProtectionTripped",
    "PSUTimeoutError",
]

__version__ = "1.0.0"
