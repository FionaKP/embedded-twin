"""Component model SDK.

A component model binds behavior to an IR component: it holds the nets its
pins touch, reacts to net changes and scheduled events, declares power draw
to the power engine, and traces its state changes. This class is the contract
that all models — hand-written today, datasheet-agent-generated later — obey.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from ..core import SimKernel, Net, Drive

if TYPE_CHECKING:
    from ..power.engine import PowerEngine

# model key -> class
REGISTRY: dict[str, type["Component"]] = {}


def register(key: str):
    def wrap(cls: type["Component"]):
        cls.MODEL_KEY = key
        REGISTRY[key] = cls
        return cls
    return wrap


class Component:
    MODEL_KEY = ""

    def __init__(self, kernel: SimKernel, ref: str, params: dict[str, Any],
                 pin_nets: dict[str, Net], power: Optional["PowerEngine"] = None):
        self.kernel = kernel
        self.ref = ref
        self.params = params
        self.pin_nets = pin_nets      # pin name AND pin number -> Net
        self.power = power
        self.state = "init"
        kernel.register(ref, self)

    # -- pins -------------------------------------------------------------
    def net(self, *candidates: str) -> Optional[Net]:
        """Net on the first matching pin name/number (case-insensitive)."""
        lower = {k.lower(): v for k, v in self.pin_nets.items()}
        for c in candidates:
            if c.lower() in lower:
                return lower[c.lower()]
        return None

    def require_net(self, *candidates: str) -> Net:
        net = self.net(*candidates)
        if net is None:
            raise KeyError(f"{self.ref}: no pin matching {candidates} "
                           f"(has {sorted(self.pin_nets)})")
        return net

    def drive(self, pin: str, d: Drive) -> None:
        self.require_net(pin).drive(f"{self.ref}.{pin}", d)

    # -- state + tracing --------------------------------------------------
    def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.kernel.trace.record(self.kernel.now, "state", self.ref, state)

    def log(self, msg: str) -> None:
        self.kernel.trace.record(self.kernel.now, "log", self.ref, msg)

    # -- power ------------------------------------------------------------
    def set_load(self, rail_net: str, amps: float) -> None:
        if self.power:
            self.power.set_load(self.ref, rail_net, amps)

    def set_dissipation(self, watts: float) -> None:
        if self.power:
            self.power.set_dissipation(self.ref, watts)

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        """Called once at simulation power-on."""
