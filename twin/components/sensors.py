"""Sensor models."""
from __future__ import annotations

import struct

from ..comm.i2c import I2CBus
from ..comm.spi import SpiBus
from .base import Component, register


@register("sensor.temp_i2c")
class TempSensorI2C(Component):
    """TMP102-style I2C temperature sensor.

    params: addr (default 0x48), i_active_ua (default 10)
    Register 0 reads two bytes: temp * 16, left-justified 12-bit (TMP102 format).
    The measured temperature tracks its own thermal node if the power engine
    has one for this ref, else the scenario can call set_temperature().
    """

    def start(self) -> None:
        scl = self.require_net("SCL")
        sda = self.require_net("SDA")
        self.addr = int(self.params.get("addr", 0x48))
        self.bus = I2CBus.on_nets(self.kernel, scl, sda)
        self.bus.attach(self.addr, self)
        self._pointer = 0
        self.temperature_c: float | None = None  # None -> follow ambient
        vdd = self.net("VDD", "V+", "VCC")
        if vdd is not None:
            self.set_load(vdd.name, self.params.get("i_active_ua", 10) * 1e-6)
        self.set_state("active")

    def set_temperature(self, temp_c: float) -> None:
        self.temperature_c = temp_c

    def _measured(self) -> float:
        if self.temperature_c is not None:
            return self.temperature_c
        if self.power:
            node = self.power.thermal.get(self.ref)
            if node:
                return node.temp_c
            return self.power.ambient_c
        return 25.0

    # -- I2C device interface --------------------------------------------
    def i2c_write(self, data: bytes) -> bool:
        if data:
            self._pointer = data[0]
        return True

    def i2c_read(self, n: int) -> bytes:
        if self._pointer == 0:
            raw = int(self._measured() * 16) << 4
            return struct.pack(">h", raw)[:n]
        return bytes(n)


@register("sensor.accel_spi")
class AccelSpi(Component):
    """LIS3DH-style SPI accelerometer.

    Register map subset: 0x0F WHO_AM_I = 0x33; 0x20-0x25 CTRL_REG1-6
    (storage); 0x28-0x2D OUT_X_L..OUT_Z_H. Address byte: bit7 = read,
    bit6 = auto-increment. Scenario/test API: set_acceleration(x, y, z) in g.
    params: i_active_ua (11)
    """

    WHO_AM_I = 0x33

    def start(self) -> None:
        sck = self.require_net("SCK", "SCL", "SPC")
        mosi = self.net("MOSI", "SDI", "SDA")
        miso = self.net("MISO", "SDO")
        cs = self.require_net("CS", "NCS", "CS_N")
        bus = SpiBus.on_nets(self.kernel, sck, mosi, miso)
        bus.attach(cs, self)
        self.regs: dict[int, int] = {0x0F: self.WHO_AM_I}
        self.set_acceleration(0.0, 0.0, 1.0)   # at rest, 1 g on Z
        self._addr = None
        self._read = False
        vdd = self.net("VDD", "VCC")
        if vdd is not None:
            self.set_load(vdd.name, self.params.get("i_active_ua", 11) * 1e-6)
        self.set_state("active")

    def set_acceleration(self, x_g: float, y_g: float, z_g: float) -> None:
        for base, g in ((0x28, x_g), (0x2A, y_g), (0x2C, z_g)):
            raw = max(-32768, min(32767, int(g * 16384)))  # +-2 g full scale
            lo, hi = struct.pack("<h", raw)
            self.regs[base] = lo
            self.regs[base + 1] = hi

    # -- SPI device interface ---------------------------------------------
    def spi_select(self, asserted: bool) -> None:
        if asserted:
            self._addr = None

    def spi_transfer(self, mosi: int) -> int:
        if self._addr is None:                 # first byte: address/command
            self._read = bool(mosi & 0x80)
            self._auto = bool(mosi & 0x40)
            self._addr = mosi & 0x3F
            return 0xFF
        addr = self._addr
        if self._auto:
            self._addr = (addr + 1) & 0x3F
        if self._read:
            return self.regs.get(addr, 0)
        self.regs[addr] = mosi
        return 0xFF
