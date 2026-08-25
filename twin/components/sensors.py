"""Sensor models."""
from __future__ import annotations

import struct

from ..comm.i2c import I2CBus
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
