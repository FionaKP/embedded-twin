"""Transaction-level I2C bus.

Devices attach at 7-bit addresses on an (SCL, SDA) net pair; masters issue
write/read transactions with realistic bus timing. Transactions are traced
(kind="i2c") on the SDA net name. Bit-level open-drain waveforms are a
deferred fidelity upgrade.
"""
from __future__ import annotations

from typing import Optional, Protocol

from ..core import SimKernel, Net
from ..core.kernel import SEC


class I2CDevice(Protocol):
    def i2c_write(self, data: bytes) -> bool: ...
    def i2c_read(self, n: int) -> bytes: ...


class I2CBus:
    def __init__(self, kernel: SimKernel, scl: Net, sda: Net, clock_hz: int = 400_000):
        self.kernel = kernel
        self.scl, self.sda = scl, sda
        self.clock_hz = clock_hz
        self.devices: dict[int, I2CDevice] = {}

    @staticmethod
    def on_nets(kernel: SimKernel, scl: Net, sda: Net, clock_hz: int = 400_000) -> "I2CBus":
        """One shared bus object per SDA net."""
        bus = getattr(sda, "_i2c_bus", None)
        if bus is None:
            bus = I2CBus(kernel, scl, sda, clock_hz)
            sda._i2c_bus = bus  # type: ignore[attr-defined]
        return bus

    def attach(self, addr: int, device: I2CDevice) -> None:
        if addr in self.devices:
            raise ValueError(f"I2C address collision at 0x{addr:02x}")
        self.devices[addr] = device

    def _bus_time(self, payload_len: int) -> int:
        bits = (1 + payload_len) * 9 + 2  # addr + bytes (9 clocks each) + start/stop
        return int(bits * SEC / self.clock_hz)

    def write(self, addr: int, data: bytes) -> bool:
        dev = self.devices.get(addr)
        ack = dev is not None and dev.i2c_write(bytes(data))
        self.kernel.trace.record(self.kernel.now, "i2c", self.sda.name,
                                 {"op": "W", "addr": addr, "data": list(data),
                                  "ack": ack, "dur_ns": self._bus_time(len(data))})
        return ack

    def read(self, addr: int, n: int) -> Optional[bytes]:
        dev = self.devices.get(addr)
        data = dev.i2c_read(n) if dev is not None else None
        self.kernel.trace.record(self.kernel.now, "i2c", self.sda.name,
                                 {"op": "R", "addr": addr,
                                  "data": list(data) if data else None,
                                  "ack": data is not None,
                                  "dur_ns": self._bus_time(n)})
        return data
