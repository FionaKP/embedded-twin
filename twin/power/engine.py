"""Power + thermal engine.

Event-integrated (not sampled): whenever any load, source, or dissipation
changes, the elapsed interval is accrued at the previous operating point.
That makes 24-hour battery sims cheap — cost scales with activity, not time.

Rails form a tree: battery/external -> regulators -> loads. Regulators
reflect their output load onto their input rail (LDO: Iin = Iout + Iq;
buck: Iin = Vout*Iout / (Vin*eff)).

Thermal: one lumped RC node per dissipating component,
T -> T_amb + P*Rth with time constant Rth*Cth.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..core import SimKernel
from ..core.kernel import SEC


@dataclass
class RailStats:
    energy_j: float = 0.0
    charge_c: float = 0.0
    peak_a: float = 0.0
    time_weighted_a: float = 0.0  # integral of I dt, for averages


@dataclass
class ThermalNode:
    rth_c_per_w: float
    cth_j_per_c: float
    temp_c: float


class Regulator:
    """Power-tree edge registered by regulator component models."""

    def __init__(self, ref: str, input_rail: str, output_rail: str,
                 reflect: Callable[[float], float],
                 dissipate: Callable[[float], float]):
        self.ref = ref
        self.input_rail = input_rail
        self.output_rail = output_rail
        self.reflect = reflect        # output amps -> input amps
        self.dissipate = dissipate    # output amps -> watts lost in the reg


class Battery:
    """Charge store registered by a battery component model."""

    def __init__(self, ref: str, rail: str, capacity_mah: float,
                 v_full: float = 4.2, v_empty: float = 3.3,
                 r_internal: float = 0.1, soc: float = 1.0):
        self.ref = ref
        self.rail = rail
        self.capacity_c = capacity_mah * 3.6  # mAh -> coulombs
        self.v_full, self.v_empty = v_full, v_empty
        self.r_internal = r_internal
        self.soc = soc
        self.drained_c = capacity_mah * 3.6 * (1 - soc)

    def voltage(self, load_a: float = 0.0) -> float:
        ocv = self.v_empty + (self.v_full - self.v_empty) * self.soc
        return max(0.0, ocv - load_a * self.r_internal)

    def drain(self, coulombs: float) -> None:
        self.drained_c += coulombs
        self.soc = max(0.0, 1.0 - self.drained_c / self.capacity_c)


class PowerEngine:
    def __init__(self, kernel: SimKernel, ambient_c: float = 25.0):
        self.kernel = kernel
        self.ambient_c = ambient_c
        self._loads: dict[tuple[str, str], float] = {}       # (ref, rail) -> A
        self._dissipation: dict[str, float] = {}             # ref -> W
        self._voltages: dict[str, float] = {}                # rail -> V
        self.regulators: list[Regulator] = []
        self.batteries: list[Battery] = []
        self.thermal: dict[str, ThermalNode] = {}
        self.rails: dict[str, RailStats] = {}
        self._last_t = 0
        self.total_time = 0

    # -- registration -----------------------------------------------------
    def set_rail_voltage(self, rail: str, volts: float) -> None:
        self._voltages[rail] = volts

    def add_regulator(self, reg: Regulator) -> None:
        self.accrue()
        self.regulators.append(reg)

    def add_battery(self, bat: Battery) -> None:
        self.accrue()
        self.batteries.append(bat)

    def add_thermal(self, ref: str, rth: float, cth: float,
                    temp_c: Optional[float] = None) -> None:
        self.thermal[ref] = ThermalNode(rth, cth, temp_c if temp_c is not None
                                        else self.ambient_c)

    # -- operating point changes ------------------------------------------
    def set_load(self, ref: str, rail: str, amps: float) -> None:
        key = (ref, rail)
        if self._loads.get(key, 0.0) == amps:
            return
        self.accrue()
        self._loads[key] = amps
        self.kernel.trace.record(self.kernel.now, "power", f"{ref}@{rail}",
                                 {"i_ma": amps * 1000})

    def set_dissipation(self, ref: str, watts: float) -> None:
        if self._dissipation.get(ref, 0.0) == watts:
            return
        self.accrue()
        self._dissipation[ref] = watts

    # -- solving ----------------------------------------------------------
    def rail_currents(self) -> dict[str, float]:
        """Direct loads plus regulator-reflected loads, leaves-first."""
        currents: dict[str, float] = {}
        for (_ref, rail), amps in self._loads.items():
            currents[rail] = currents.get(rail, 0.0) + amps
        # propagate through the regulator tree (loop-bounded for safety)
        remaining = list(self.regulators)
        for _ in range(len(remaining) + 1):
            if not remaining:
                break
            downstream_inputs = {r.input_rail for r in remaining}
            progressed = []
            for reg in remaining:
                # a regulator can be reflected once every regulator feeding
                # from its output rail has been reflected already
                if reg.output_rail in {r2.input_rail for r2 in remaining if r2 is not reg}:
                    continue
                out_a = currents.get(reg.output_rail, 0.0)
                currents[reg.input_rail] = currents.get(reg.input_rail, 0.0) + reg.reflect(out_a)
                progressed.append(reg)
            if not progressed:  # cycle — reflect the rest in one pass
                for reg in remaining:
                    out_a = currents.get(reg.output_rail, 0.0)
                    currents[reg.input_rail] = currents.get(reg.input_rail, 0.0) + reg.reflect(out_a)
                remaining = []
                break
            remaining = [r for r in remaining if r not in progressed]
        return currents

    def accrue(self) -> None:
        now = self.kernel.now
        dt_ns = now - self._last_t
        if dt_ns <= 0:
            return
        dt = dt_ns / SEC
        self._last_t = now
        self.total_time += dt_ns

        currents = self.rail_currents()
        for rail, amps in currents.items():
            stats = self.rails.setdefault(rail, RailStats())
            v = self._voltages.get(rail, 0.0)
            stats.energy_j += v * amps * dt
            stats.charge_c += amps * dt
            stats.time_weighted_a += amps * dt
            stats.peak_a = max(stats.peak_a, amps)

        for bat in self.batteries:
            amps = currents.get(bat.rail, 0.0)
            bat.drain(amps * dt)
            self._voltages[bat.rail] = bat.voltage(amps)

        # regulator dissipation feeds thermal
        dissipation = dict(self._dissipation)
        for reg in self.regulators:
            out_a = currents.get(reg.output_rail, 0.0)
            dissipation[reg.ref] = dissipation.get(reg.ref, 0.0) + reg.dissipate(out_a)

        for ref, node in self.thermal.items():
            p = dissipation.get(ref, 0.0)
            target = self.ambient_c + p * node.rth_c_per_w
            tau = node.rth_c_per_w * node.cth_j_per_c
            if tau > 0:
                node.temp_c = target + (node.temp_c - target) * math.exp(-dt / tau)
            else:
                node.temp_c = target

    # -- reporting --------------------------------------------------------
    def report(self) -> dict:
        self.accrue()
        hours = self.total_time / SEC / 3600 or None
        rails = {}
        for rail, s in self.rails.items():
            secs = self.total_time / SEC
            rails[rail] = {
                "avg_ma": (s.time_weighted_a / secs * 1000) if secs else 0.0,
                "peak_ma": s.peak_a * 1000,
                "energy_mwh": s.energy_j / 3.6,
                "charge_mah": s.charge_c / 3.6,
            }
        return {
            "sim_hours": self.total_time / SEC / 3600,
            "rails": rails,
            "batteries": [{
                "ref": b.ref, "rail": b.rail, "soc": round(b.soc, 4),
                "voltage": round(b.voltage(), 3),
                "est_runtime_h": (round(self.total_time / SEC / 3600 /
                                        max(1e-9, (1 - b.soc)), 1)
                                  if b.soc < 1 else None),
            } for b in self.batteries],
            "thermal": {ref: round(n.temp_c, 1) for ref, n in self.thermal.items()},
        }
