"""
Transport layer for the BMS link.

Two transports with the same interface:
  * TcpTransport    -> talks to the RS232/485-Ethernet gateway (TCP server)
  * SerialTransport -> talks to a local/virtual COM port (needs pyserial)

Responses arrive fragmented; `query` reassembles bytes until the CR (0x0D)
terminator or a timeout, then returns the complete frame.
"""
from __future__ import annotations

import socket
import time
from typing import Optional

from .protocol import EOI


class TransportError(Exception):
    pass


class BaseTransport:
    def open(self) -> None: ...
    def close(self) -> None: ...
    def _write(self, data: bytes) -> None: ...
    def _read(self, n: int) -> bytes: ...

    def query(self, request: bytes, timeout: float = 1.0) -> bytes:
        """Send a request and read a full frame (terminated by CR)."""
        self._write(request)
        buf = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = self._read(256)
            if chunk:
                buf += chunk
                if buf and buf[-1] == EOI:
                    return bytes(buf)
            else:
                time.sleep(0.01)
        if buf:
            return bytes(buf)            # partial; caller validates checksum
        raise TransportError("no response (timeout)")


class TcpTransport(BaseTransport):
    def __init__(self, host: str, port: int = 9999, connect_timeout: float = 4.0,
                 read_timeout: float = 0.3):
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.sock: Optional[socket.socket] = None
        self._backoff = 1.0
        self._next_try = 0.0

    def open(self) -> None:
        self.close()
        now = time.monotonic()
        if now < self._next_try:
            raise TransportError("reconnect backoff")
        try:
            s = socket.create_connection((self.host, self.port), self.connect_timeout)
            s.settimeout(self.read_timeout)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            try:
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            self.sock = s
            self._backoff = 1.0           # reset after a successful connect
        except OSError as e:
            self._next_try = now + self._backoff
            self._backoff = min(self._backoff * 2.0, 15.0)   # 1→2→4→8→15s, capped
            raise TransportError(f"connect failed: {e}")

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def _ensure(self) -> None:
        if self.sock is None:
            self.open()

    def _drain(self) -> None:
        """Discard stale bytes without blocking; detect a closed peer."""
        if self.sock is None:
            return
        self.sock.setblocking(False)
        try:
            while True:
                if not self.sock.recv(4096):   # b'' = peer closed cleanly
                    raise TransportError("peer closed connection")
        except (BlockingIOError, InterruptedError):
            pass                                # nothing pending = healthy
        finally:
            if self.sock:
                self.sock.setblocking(True)
                self.sock.settimeout(self.read_timeout)

    def _write(self, data: bytes) -> None:
        self._ensure()
        try:
            self._drain()
            self.sock.sendall(data)
        except (OSError, TransportError) as e:
            self.close()
            raise TransportError(f"tcp write failed: {e}")

    def _read(self, n: int) -> bytes:
        try:
            return self.sock.recv(n)
        except socket.timeout:
            return b""
        except OSError as e:
            self.close()
            raise TransportError(f"tcp read failed: {e}")


class SerialTransport(BaseTransport):
    def __init__(self, port: str, baudrate: int = 9600):
        self.port = port
        self.baudrate = baudrate
        self.ser = None

    def open(self) -> None:
        try:
            import serial  # pyserial
        except ImportError:
            raise TransportError("pyserial not installed (pip install pyserial)")
        self.close()
        self.ser = serial.Serial(
            self.port, self.baudrate, bytesize=8, parity="N",
            stopbits=1, timeout=0.2,
        )

    def close(self) -> None:
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def _ensure(self) -> None:
        if self.ser is None:
            self.open()

    def _write(self, data: bytes) -> None:
        self._ensure()
        try:
            self.ser.reset_input_buffer()
            self.ser.write(data)
        except Exception as e:
            self.close()
            raise TransportError(f"serial write failed: {e}")

    def _read(self, n: int) -> bytes:
        try:
            return self.ser.read(n)
        except Exception as e:
            self.close()
            raise TransportError(f"serial read failed: {e}")


def make_transport(cfg: dict) -> BaseTransport:
    kind = cfg.get("type", "tcp").lower()
    if kind == "tcp":
        return TcpTransport(cfg["host"], int(cfg.get("port", 9999)))
    if kind == "serial":
        return SerialTransport(cfg["port"], int(cfg.get("baudrate", 9600)))
    raise TransportError(f"unknown transport type: {kind}")
