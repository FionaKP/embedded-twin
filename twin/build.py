"""Build a runnable twin from a Board IR: nets, bound component models,
power engine, ground/supply seeding."""
from __future__ import annotations

from dataclasses import dataclass, field

from .core import SimKernel, Net, Drive
from .components.base import REGISTRY, Component
from .ir import BoardIR
from .power import PowerEngine

# importing the stdlib modules populates the registry
from .components import passives, power_parts, inputs, sensors, mcu  # noqa: F401


@dataclass
class BoardTwin:
    kernel: SimKernel
    power: PowerEngine
    ir: BoardIR
    nets: dict[str, Net] = field(default_factory=dict)
    components: dict[str, Component] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def net(self, name: str) -> Net:
        return self.nets[name]

    def comp(self, ref: str) -> Component:
        return self.components[ref]

    def start(self) -> None:
        self.kernel.start()
        # settle the power-on cascade (zero-delay events only); periodic
        # activity stays queued for the caller's run_until()
        self.kernel.run_until(self.kernel.now)


def build_twin(ir: BoardIR, seed: int = 0, ambient_c: float = 25.0,
               external_supplies: dict[str, float] | None = None) -> BoardTwin:
    kernel = SimKernel(seed=seed)
    power = PowerEngine(kernel, ambient_c=ambient_c)
    twin = BoardTwin(kernel=kernel, power=power, ir=ir)

    for net_ir in ir.nets.values():
        twin.nets[net_ir.name] = Net(kernel, net_ir.name, net_ir.net_class)

    # ground reference
    for net in twin.nets.values():
        if net.net_class == "ground":
            net.drive("__gnd__", Drive.low())

    # bench supplies (USB, external power, debug rails)
    for name, volts in (external_supplies or {}).items():
        if name not in twin.nets:
            twin.warnings.append(f"external supply on unknown net {name!r}")
            continue
        twin.nets[name].drive("__supply__", Drive.high(volts))
        power.set_rail_voltage(name, volts)

    for comp_ir in ir.components.values():
        if not comp_ir.model:
            twin.warnings.append(f"{comp_ir.ref}: unbound (open circuit in sim)")
            continue
        cls = REGISTRY.get(comp_ir.model)
        if cls is None:
            twin.warnings.append(
                f"{comp_ir.ref}: model {comp_ir.model!r} not in registry "
                f"(open circuit in sim)")
            continue
        pin_nets: dict[str, Net] = {}
        for pin_num, net_name in ir.nets_of(comp_ir.ref).items():
            net = twin.nets[net_name]
            pin_nets[pin_num] = net
            pin_name = comp_ir.pins.get(pin_num)
            if pin_name and pin_name not in ("~", ""):
                pin_nets.setdefault(pin_name, net)
        params = dict(comp_ir.params)
        params.setdefault("value", comp_ir.value)
        twin.components[comp_ir.ref] = cls(kernel, comp_ir.ref, params,
                                           pin_nets, power)
    for w in twin.warnings:
        kernel.trace.record(0, "log", "build", w)
    return twin
