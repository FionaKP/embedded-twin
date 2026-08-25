"""Nets: the wires of the twin.

A net resolves the drives of everything connected to it into one state:
a digital ``Level`` (0/1/Z/X) plus an optional analog voltage. Resolution
is by drive strength — STRONG beats PULL beats HIZ — and same-strength
disagreement is contention (X), which is traced as a probe-visible fault.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from .kernel import SimKernel


class Level(Enum):
    LOW = "0"
    HIGH = "1"
    Z = "Z"      # undriven
    X = "X"      # contention / unknown

    def __repr__(self) -> str:  # keep traces compact
        return self.value


class Strength(IntEnum):
    HIZ = 0
    PULL = 1     # pull-up/down resistors, weak keepers
    STRONG = 2   # push-pull outputs, rails


@dataclass(frozen=True)
class Drive:
    level: Level
    strength: Strength
    voltage: Optional[float] = None  # analog value, when meaningful

    @staticmethod
    def high(voltage: float | None = None) -> "Drive":
        return Drive(Level.HIGH, Strength.STRONG, voltage)

    @staticmethod
    def low() -> "Drive":
        return Drive(Level.LOW, Strength.STRONG, 0.0)

    @staticmethod
    def release() -> "Drive":
        return Drive(Level.Z, Strength.HIZ)

    @staticmethod
    def pull_up(voltage: float | None = None) -> "Drive":
        return Drive(Level.HIGH, Strength.PULL, voltage)

    @staticmethod
    def pull_down() -> "Drive":
        return Drive(Level.LOW, Strength.PULL, 0.0)

    @staticmethod
    def analog(voltage: float) -> "Drive":
        return Drive(Level.HIGH if voltage > 0 else Level.LOW, Strength.STRONG, voltage)


class Net:
    def __init__(self, kernel: "SimKernel", name: str, net_class: str = "signal"):
        self.kernel = kernel
        self.name = name
        self.net_class = net_class  # signal | power | ground
        self._drives: dict[str, Drive] = {}
        self._listeners: list[Callable[["Net"], None]] = []
        self.level: Level = Level.Z
        self.voltage: Optional[float] = None
        self.contention: bool = False

    # -- wiring -----------------------------------------------------------
    def listen(self, fn: Callable[["Net"], None]) -> None:
        self._listeners.append(fn)

    def drive(self, driver_id: str, drive: Drive) -> None:
        """Set one driver's contribution and re-resolve.

        Propagation to listeners is scheduled as a zero-delay event so that
        cascaded updates settle iteratively instead of recursing.
        """
        if self._drives.get(driver_id) == drive:
            return
        self._drives[driver_id] = drive
        self._resolve()

    # -- resolution -------------------------------------------------------
    def _resolve(self) -> None:
        best = Strength.HIZ
        winners: list[Drive] = []
        for d in self._drives.values():
            if d.strength > best:
                best, winners = d.strength, [d]
            elif d.strength == best and d.strength > Strength.HIZ:
                winners.append(d)

        if not winners:
            new_level, new_voltage, contention = Level.Z, None, False
        else:
            levels = {d.level for d in winners}
            if len(levels) == 1:
                new_level = winners[0].level
                voltages = [d.voltage for d in winners if d.voltage is not None]
                new_voltage = max(voltages) if voltages else None
                contention = False
            else:
                new_level, new_voltage, contention = Level.X, None, True

        if (new_level, new_voltage, contention) == (self.level, self.voltage, self.contention):
            return
        self.level, self.voltage, self.contention = new_level, new_voltage, contention
        self.kernel.trace.record(self.kernel.now, "net", self.name,
                                 {"level": new_level.value, "v": new_voltage,
                                  **({"contention": True} if contention else {})})
        self.kernel.schedule(0, self._notify)

    def _notify(self) -> None:
        for fn in list(self._listeners):
            fn(self)

    # -- convenience ------------------------------------------------------
    @property
    def is_high(self) -> bool:
        return self.level == Level.HIGH

    @property
    def is_low(self) -> bool:
        return self.level == Level.LOW

    def __repr__(self) -> str:
        return f"<Net {self.name}={self.level.value}{'' if self.voltage is None else f'({self.voltage:.3g}V)'}>"
