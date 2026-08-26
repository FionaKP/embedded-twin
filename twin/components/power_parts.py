"""Power-chain component models: battery, LDO, buck."""
from __future__ import annotations

from ..core import Drive
from ..power.engine import Battery as BatteryState, Regulator as RegulatorEdge
from .base import Component, register


@register("power.battery")
class Battery(Component):
    """params: capacity_mah (default 1000), v_full, v_empty, r_internal, soc"""

    def start(self) -> None:
        self.pos = self.require_net("+", "1", "VBAT", "POS")
        self.state_model = BatteryState(
            self.ref, self.pos.name,
            capacity_mah=self.params.get("capacity_mah", 1000),
            v_full=self.params.get("v_full", 4.2),
            v_empty=self.params.get("v_empty", 3.3),
            r_internal=self.params.get("r_internal", 0.1),
            soc=self.params.get("soc", 1.0),
        )
        if self.power:
            self.power.add_battery(self.state_model)
            self.power.set_rail_voltage(self.pos.name, self.state_model.voltage())
        self.pos.drive(f"{self.ref}.+", Drive.high(self.state_model.voltage()))
        self.set_state("supplying")


class _RegulatorBase(Component):
    IQ_A = 50e-6

    def start(self) -> None:
        self.vin = self.require_net("VIN", "IN")
        self.vout = self.require_net("VOUT", "OUT")
        self.en = self.net("EN", "CE", "SHDN")
        self.vout_v = float(self.params.get("vout", 3.3))
        self.vin.listen(lambda _n: self._update())
        if self.en is not None:
            self.en.listen(lambda _n: self._update())
        if self.power:
            self.power.add_regulator(RegulatorEdge(
                self.ref, self.vin.name, self.vout.name,
                reflect=self._reflect, dissipate=self._dissipate))
            self.power.set_rail_voltage(self.vout.name, self.vout_v)
        self.set_state("off")
        self._update()

    def _enabled(self) -> bool:
        vin_ok = self.vin.is_high and (self.vin.voltage or 0) >= self.vout_v * 0.8
        en_ok = self.en is None or self.en.is_high or self.en.level.value == "Z"
        return vin_ok and en_ok

    def _update(self) -> None:
        if self._enabled():
            self.vout.drive(f"{self.ref}.VOUT", Drive.high(self.vout_v))
            self.set_state("regulating")
        else:
            self.vout.drive(f"{self.ref}.VOUT", Drive.release())
            self.set_state("off")

    def _reflect(self, out_a: float) -> float:
        raise NotImplementedError

    def _dissipate(self, out_a: float) -> float:
        raise NotImplementedError

    def _vin_v(self) -> float:
        return self.vin.voltage or self.vout_v


@register("regulator.ldo")
class Ldo(_RegulatorBase):
    """params: vout, iq_ua (default 50), rth (°C/W, default 250 SOT-23)"""

    def start(self) -> None:
        super().start()
        self.IQ_A = self.params.get("iq_ua", 50) * 1e-6
        if self.power:
            self.power.add_thermal(self.ref, self.params.get("rth", 250),
                                   self.params.get("cth", 0.05))

    def _reflect(self, out_a: float) -> float:
        return out_a + (self.IQ_A if self.state == "regulating" else 1e-6)

    def _dissipate(self, out_a: float) -> float:
        return max(0.0, self._vin_v() - self.vout_v) * out_a


@register("regulator.buck")
class Buck(_RegulatorBase):
    """params: vout, efficiency (default 0.9), iq_ua (default 30), rth"""

    def start(self) -> None:
        super().start()
        self.eff = self.params.get("efficiency", 0.9)
        self.IQ_A = self.params.get("iq_ua", 30) * 1e-6
        if self.power:
            self.power.add_thermal(self.ref, self.params.get("rth", 80),
                                   self.params.get("cth", 0.1))

    def _reflect(self, out_a: float) -> float:
        vin = max(self._vin_v(), 0.1)
        return (self.vout_v * out_a) / (vin * self.eff) + self.IQ_A

    def _dissipate(self, out_a: float) -> float:
        return self.vout_v * out_a * (1 - self.eff) / max(self.eff, 0.1)


@register("power.pmos_loadswitch")
class PmosLoadSwitch(Component):
    """P-MOSFET high-side switch / power mux (source follows drain when the
    gate is pulled low — the classic USB-vs-battery ORing arrangement).

    Pins: G (gate), D (drain, supply side), S (source, load side).
    Conducts D->S when G is not high; registers a power-tree edge that
    reflects the load only while conducting. params: r_on (ignored at
    event resolution).
    """

    def start(self) -> None:
        from ..core import Level, Strength
        self._Level, self._Strength = Level, Strength
        self.g = self.require_net("G")
        self.d = self.require_net("D")
        self.s = self.require_net("S")
        self.on = False
        self.g.listen(lambda _n: self._update())
        self.d.listen(lambda _n: self._update())
        if self.power and self.d.net_class == "power" and self.s.net_class == "power":
            from ..power.engine import Regulator
            self.power.add_regulator(Regulator(
                self.ref, self.d.name, self.s.name,
                reflect=lambda out_a: out_a if self.on else 0.0,
                dissipate=lambda out_a: 0.0))
        self._update()

    def _update(self) -> None:
        gate_off = self.g.is_high
        if not gate_off and self.d.is_high:
            v = self.d.voltage
            self.s.drive(f"{self.ref}.S",
                         Drive(self._Level.HIGH, self._Strength.PULL, v))
            self.on = True
            if self.power and v is not None:
                self.power.set_rail_voltage(self.s.name, v)
        else:
            self.s.drive(f"{self.ref}.S", Drive.release())
            self.on = False
        self.set_state("on" if self.on else "off")
