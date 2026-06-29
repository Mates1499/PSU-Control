"""Backward-compatible shim.

The simulator now lives in :mod:`psu_control.simulator` so the web UI and other
callers can reuse it. Tests continue to import ``MockInstrument`` from here.
"""

from psu_control.simulator import MockInstrument, SimulatedInstrument

__all__ = ["MockInstrument", "SimulatedInstrument"]
