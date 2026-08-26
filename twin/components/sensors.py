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


@register("memory.spi_flash")
class SpiFlash(Component):
    """Generic SPI NOR flash (W25Q/GD25Q-class command subset).

    Commands: 0x9F JEDEC ID, 0x05 read status, 0x06 WREN, 0x03 read
    (24-bit address), 0x02 page program. Contents live in a sparse dict.
    params: jedec (default [0xC8, 0x40, 0x15] = GD25Q16), i_active_ua (15)
    """

    def start(self) -> None:
        sck = self.require_net("SCK", "CLK")
        mosi = self.net("MOSI", "SDI", "DI", "IO0")
        miso = self.net("MISO", "SDO", "DO", "IO1")
        cs = self.require_net("CS", "SSEL", "NCS", "CS#", "!CS", "CS_N")
        bus = SpiBus.on_nets(self.kernel, sck, mosi, miso)
        bus.attach(cs, self)
        self.jedec = list(self.params.get("jedec", [0xC8, 0x40, 0x15]))
        self.mem: dict[int, int] = {}
        self.wren = False
        self._cmd = None
        self._buf: list[int] = []
        vdd = self.net("VCC", "VDD")
        if vdd is not None:
            self.set_load(vdd.name, self.params.get("i_active_ua", 15) * 1e-6)
        self.set_state("ready")

    # -- SPI device interface ---------------------------------------------
    def spi_select(self, asserted: bool) -> None:
        if asserted:
            self._cmd = None
            self._buf = []

    def spi_transfer(self, mosi: int) -> int:
        if self._cmd is None:
            self._cmd = mosi
            if self._cmd == 0x06:
                self.wren = True
            return 0xFF
        self._buf.append(mosi)
        n = len(self._buf)
        if self._cmd == 0x9F:
            return self.jedec[n - 1] if n <= len(self.jedec) else 0xFF
        if self._cmd == 0x05:
            return 0x00                       # never busy at event resolution
        if self._cmd == 0x03 and n > 3:       # read after 3 address bytes
            addr = (self._buf[0] << 16) | (self._buf[1] << 8) | self._buf[2]
            return self.mem.get(addr + (n - 4), 0xFF)
        if self._cmd == 0x02 and n > 3 and self.wren:
            addr = (self._buf[0] << 16) | (self._buf[1] << 8) | self._buf[2]
            self.mem[addr + (n - 4)] = mosi
        return 0xFF


@register("led.ws2812")
class Ws2812(Component):
    """Addressable RGB LED (NeoPixel) — power-accounted stub.

    The 800 kHz single-wire protocol is bit-banged with sub-microsecond
    timing the cycle-approximate CPU model cannot represent, so pixel data
    is not decoded (FUTURE.md). Idle current is modeled; DI edges are
    visible in the trace for probing.
    params: i_idle_ma (0.7 per device), n (chain length, default 1)
    """

    def start(self) -> None:
        vdd = self.net("VDD", "VCC", "5VDC")
        if vdd is not None:
            self.set_load(vdd.name,
                          self.params.get("i_idle_ma", 0.7) * 1e-3
                          * self.params.get("n", 1))
        self.set_state("idle")
