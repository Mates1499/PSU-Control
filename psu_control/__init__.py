"""Python control library for the ITECH IT-N6332B triple-channel DC power supply.

The IT-N6332B belongs to ITECH's IT-N6300 / IT6300 family of triple-channel
programmable DC power supplies. It provides three independent, isolated
source-only output channels (CH1 & CH2: 0-30 V / 0-6 A / 180 W, CH3:
0-5 V / 0-3 A / 15 W) and is fully remote-controllable over LAN / USB / GPIB /
RS-232 using SCPI.

Typical usage::

    from psu_control import ITN6332B

    with ITN6332B.open_tcp("192.168.1.50") as psu:
        print(psu.idn())
        psu.reset()
        psu.ch1.apply(12.0, 2.0)     # 12 V, 2 A limit
        psu.ch1.output_on()
        print(psu.ch1.measure())     # 12.00 V, 0.50 A, 6.0 W

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
    ChannelSpec,
    Measurement,
    IT_N6332B_CHANNELS,
)

__all__ = [
    "ITN6332B",
    "Channel",
    "ChannelSpec",
    "Measurement",
    "IT_N6332B_CHANNELS",
    "ScpiConnection",
    "PSUError",
    "PSUConnectionError",
    "PSUCommandError",
    "PSUProtectionTripped",
    "PSUTimeoutError",
]

__version__ = "2.0.0"
