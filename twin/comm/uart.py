"""Transaction-level UART over nets.

Bytes ride the TX net with real baud timing (10 bits/byte, serialized),
and every byte is traced under the net's name (kind="uart") — so the
virtual probe shows serial traffic on the same wire a scope clip would.
Bit-level waveforms are a deferred fidelity upgrade (FUTURE.md).
"""
from __future__ import annotations

from typing import Callable, Optional

from ..core import SimKernel, Net
from ..core.kernel import SEC


class Uart:
    def __init__(self, kernel: SimKernel, owner: str,
                 tx_net: Optional[Net], rx_net: Optional[Net],
                 baud: int = 115200):
        self.kernel = kernel
        self.owner = owner
        self.tx_net = tx_net
        self.rx_net = rx_net
        self.baud = baud
        self._rx_handler: Optional[Callable[[int], None]] = None
        self._tx_busy_until = 0
        if rx_net is not None:
            if not hasattr(rx_net, "_uart_ports"):
                rx_net._uart_ports = []  # type: ignore[attr-defined]
            rx_net._uart_ports.append(self)  # type: ignore[attr-defined]

    def on_byte(self, fn: Callable[[int], None]) -> None:
        self._rx_handler = fn

    def send(self, data: bytes) -> None:
        if self.tx_net is None:
            return
        byte_ns = int(10 * SEC / self.baud)
        start = max(self.kernel.now, self._tx_busy_until)
        for i, b in enumerate(data):
            t = start + (i + 1) * byte_ns
            self.kernel.schedule_at(t, self._deliver, b)
        self._tx_busy_until = start + len(data) * byte_ns

    def _deliver(self, b: int) -> None:
        self.kernel.trace.record(self.kernel.now, "uart", self.tx_net.name, b)
        for port in getattr(self.tx_net, "_uart_ports", []):
            if port is not self and port._rx_handler:
                port._rx_handler(b)


class LineAssembler:
    """Utility: accumulate bytes into lines for AT/NMEA style protocols."""

    def __init__(self, on_line: Callable[[str], None], terminator: bytes = b"\r\n"):
        self.on_line = on_line
        self.terminator = terminator
        self._buf = bytearray()

    def feed(self, b: int) -> None:
        self._buf.append(b)
        if self._buf.endswith(self.terminator):
            line = self._buf[:-len(self.terminator)].decode(errors="replace")
            self._buf.clear()
            if line:
                self.on_line(line)
