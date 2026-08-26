"""Transaction-level SPI bus.

Full-duplex byte transfers routed to whichever attached device has its CS
net asserted (low). Devices implement spi_transfer(mosi_byte) -> miso_byte
and optionally spi_select(asserted) to reset per-transaction state.
Transfers are traced (kind="spi") on the MOSI net. Bit-level SPI waveforms
are a deferred fidelity upgrade, like UART/I2C (FUTURE.md).
"""
from __future__ import annotations

from typing import Optional, Protocol

from ..core import SimKernel, Net


class SpiDevice(Protocol):
    def spi_transfer(self, mosi: int) -> int: ...


class SpiBus:
    def __init__(self, kernel: SimKernel, sck: Net, mosi: Optional[Net],
                 miso: Optional[Net]):
        self.kernel = kernel
        self.sck, self.mosi, self.miso = sck, mosi, miso
        self.devices: list[tuple[Net, SpiDevice]] = []

    @staticmethod
    def on_nets(kernel: SimKernel, sck: Net, mosi: Optional[Net],
                miso: Optional[Net]) -> "SpiBus":
        bus = getattr(sck, "_spi_bus", None)
        if bus is None:
            bus = SpiBus(kernel, sck, mosi, miso)
            sck._spi_bus = bus  # type: ignore[attr-defined]
        return bus

    def attach(self, cs_net: Net, device: SpiDevice) -> None:
        self.devices.append((cs_net, device))
        select = getattr(device, "spi_select", None)
        if select is not None:
            cs_net.listen(lambda net: select(net.is_low))

    def transfer(self, mosi_byte: int) -> int:
        miso_byte = 0xFF
        selected = None
        for cs_net, dev in self.devices:
            if cs_net.is_low:
                miso_byte = dev.spi_transfer(mosi_byte & 0xFF) & 0xFF
                selected = dev
                break
        name = self.mosi.name if self.mosi is not None else self.sck.name
        self.kernel.trace.record(self.kernel.now, "spi", name,
                                 {"mosi": mosi_byte & 0xFF, "miso": miso_byte,
                                  "sel": selected is not None})
        return miso_byte
