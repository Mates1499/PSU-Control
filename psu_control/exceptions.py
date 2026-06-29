"""Exception hierarchy for the PSU control library."""

from __future__ import annotations


class PSUError(Exception):
    """Base class for all errors raised by this library."""


class PSUConnectionError(PSUError):
    """Raised when a connection to the instrument cannot be established."""


class PSUTimeoutError(PSUError):
    """Raised when the instrument does not respond within the timeout."""


class PSUCommandError(PSUError):
    """Raised when the instrument reports an error in its error queue.

    Attributes:
        code: The numeric SCPI error code returned by ``SYSTem:ERRor?``.
        message: The human-readable error string from the instrument.
    """

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"Instrument error {code}: {message}")


class PSUProtectionTripped(PSUError):
    """Raised when a protection (OVP/OCP/OPP/OTP) has tripped the output."""
