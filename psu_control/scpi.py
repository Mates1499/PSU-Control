"""Low-level SCPI connection layer.

This module provides :class:`ScpiConnection`, a thin transport abstraction that
talks raw SCPI to the instrument. Two backends are supported:

* **PyVISA** (recommended) -- handles USB-TMC, TCPIP/VXI-11, GPIB and serial via
  a single resource string, e.g. ``TCPIP0::192.168.200.100::inst0::INSTR`` or
  ``USB0::0x2EC7::0x6300::...::INSTR``.
* **Raw TCP socket** -- a dependency-free fallback that speaks the SCPI raw
  socket protocol most ITECH instruments expose on TCP port 30000.

Both backends present the same ``write`` / ``query`` / ``read`` API so the
higher-level driver does not care which is in use.
"""

from __future__ import annotations

import socket
from typing import Optional

from .exceptions import PSUConnectionError, PSUTimeoutError

# ITECH instruments expose a raw SCPI socket on this TCP port by default.
DEFAULT_SCPI_PORT = 30000
DEFAULT_TIMEOUT_S = 5.0
_TERMINATOR = "\n"


class ScpiConnection:
    """A transport-agnostic SCPI connection.

    Prefer the classmethod constructors :meth:`from_visa` or :meth:`from_tcp`
    rather than calling ``__init__`` directly.
    """

    def __init__(self, backend: "_Backend"):
        self._backend = backend

    # -- constructors -----------------------------------------------------

    @classmethod
    def from_visa(
        cls,
        resource: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        visa_library: str = "@py",
        read_termination: str = _TERMINATOR,
        write_termination: str = _TERMINATOR,
    ) -> "ScpiConnection":
        """Open a connection through PyVISA.

        Args:
            resource: A VISA resource string. Examples::
                "TCPIP0::192.168.200.100::inst0::INSTR"
                "USB0::0x2EC7::0x6300::800001::INSTR"
                "GPIB0::5::INSTR"
                "ASRL/dev/ttyUSB0::INSTR"
            timeout: I/O timeout in seconds.
            visa_library: VISA backend. Defaults to ``"@py"`` (pyvisa-py,
                pure Python). Pass ``""`` to use the system VISA (NI/Keysight).
        """
        return cls(
            _VisaBackend(
                resource,
                timeout=timeout,
                visa_library=visa_library,
                read_termination=read_termination,
                write_termination=write_termination,
            )
        )

    @classmethod
    def from_tcp(
        cls,
        host: str,
        port: int = DEFAULT_SCPI_PORT,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> "ScpiConnection":
        """Open a raw TCP socket connection (no VISA required).

        Args:
            host: Instrument IP address or hostname.
            port: Raw SCPI socket port (ITECH default is 30000).
            timeout: Socket timeout in seconds.
        """
        return cls(_SocketBackend(host, port, timeout=timeout))

    # -- core I/O ---------------------------------------------------------

    def write(self, command: str) -> None:
        """Send a command that does not return a response."""
        self._backend.write(command)

    def read(self) -> str:
        """Read a single terminated response line."""
        return self._backend.read()

    def query(self, command: str) -> str:
        """Send a query and return its response, stripped of whitespace."""
        return self._backend.query(command)

    # -- lifecycle --------------------------------------------------------

    @property
    def timeout(self) -> float:
        return self._backend.timeout

    @timeout.setter
    def timeout(self, value: float) -> None:
        self._backend.timeout = value

    def close(self) -> None:
        self._backend.close()

    def __enter__(self) -> "ScpiConnection":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"ScpiConnection({self._backend!r})"


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------


class _Backend:
    """Interface implemented by transport backends."""

    timeout: float

    def write(self, command: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def read(self) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def query(self, command: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class _VisaBackend(_Backend):
    """PyVISA-backed transport."""

    def __init__(
        self,
        resource: str,
        *,
        timeout: float,
        visa_library: str,
        read_termination: str,
        write_termination: str,
    ):
        try:
            import pyvisa
        except ImportError as exc:  # pragma: no cover - import guard
            raise PSUConnectionError(
                "PyVISA is not installed. Install it with `pip install pyvisa "
                "pyvisa-py`, or use ScpiConnection.from_tcp() for a raw socket."
            ) from exc

        self._resource_name = resource
        try:
            rm = pyvisa.ResourceManager(visa_library)
            self._inst = rm.open_resource(resource)
        except Exception as exc:
            raise PSUConnectionError(
                f"Could not open VISA resource {resource!r}: {exc}"
            ) from exc

        self._inst.read_termination = read_termination
        self._inst.write_termination = write_termination
        self.timeout = timeout

    @property
    def timeout(self) -> float:
        return self._inst.timeout / 1000.0

    @timeout.setter
    def timeout(self, value: float) -> None:
        self._inst.timeout = int(value * 1000)  # PyVISA uses milliseconds

    def write(self, command: str) -> None:
        self._inst.write(command)

    def read(self) -> str:
        return self._inst.read().strip()

    def query(self, command: str) -> str:
        try:
            return self._inst.query(command).strip()
        except Exception as exc:  # VisaIOError on timeout, etc.
            if "timeout" in str(exc).lower():
                raise PSUTimeoutError(
                    f"Timed out waiting for response to {command!r}"
                ) from exc
            raise

    def close(self) -> None:
        try:
            self._inst.close()
        except Exception:
            pass

    def __repr__(self) -> str:
        return f"VISA<{self._resource_name}>"


class _SocketBackend(_Backend):
    """Dependency-free raw TCP socket transport."""

    def __init__(self, host: str, port: int, *, timeout: float):
        self._host = host
        self._port = port
        self._buffer = b""
        try:
            self._sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            raise PSUConnectionError(
                f"Could not connect to {host}:{port}: {exc}"
            ) from exc
        self._sock.settimeout(timeout)
        self._timeout = timeout

    @property
    def timeout(self) -> float:
        return self._timeout

    @timeout.setter
    def timeout(self, value: float) -> None:
        self._timeout = value
        self._sock.settimeout(value)

    def write(self, command: str) -> None:
        if not command.endswith(_TERMINATOR):
            command += _TERMINATOR
        try:
            self._sock.sendall(command.encode("ascii"))
        except OSError as exc:
            raise PSUConnectionError(f"Send failed: {exc}") from exc

    def read(self) -> str:
        term = _TERMINATOR.encode("ascii")
        while term not in self._buffer:
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout as exc:
                raise PSUTimeoutError("Timed out waiting for response") from exc
            except OSError as exc:
                raise PSUConnectionError(f"Receive failed: {exc}") from exc
            if not chunk:
                raise PSUConnectionError("Connection closed by instrument")
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(term)
        return line.decode("ascii", errors="replace").strip()

    def query(self, command: str) -> str:
        self.write(command)
        return self.read()

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def __repr__(self) -> str:
        return f"TCP<{self._host}:{self._port}>"
