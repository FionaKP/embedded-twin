"""KiCad netlist (.net, S-expression export) -> Board IR."""
from __future__ import annotations

from pathlib import Path

from ..ir import BoardIR, ComponentIR, NetIR, NetNode
from . import sexpr

_GROUND_NAMES = {"GND", "GNDA", "GNDD", "AGND", "DGND", "VSS", "GROUND", "0"}
_POWER_PREFIXES = ("VCC", "VDD", "VBAT", "VBUS", "VSYS", "VIN", "VOUT", "+")


def classify_net(name: str) -> str:
    base = name.lstrip("/").split("/")[-1].upper()
    if base in _GROUND_NAMES:
        return "ground"
    if base.startswith(_POWER_PREFIXES):
        return "power"
    return "signal"


def parse_kicad_netlist(path: str | Path, name: str | None = None) -> BoardIR:
    text = Path(path).read_text()
    root = sexpr.parse(text)
    if not (isinstance(root, list) and root and root[0] == "export"):
        raise ValueError(f"{path}: not a KiCad netlist export")

    board = BoardIR(name=name or Path(path).stem,
                    metadata={"source": str(path), "format": "kicad-netlist"})

    comps = sexpr.find(root, "components") or []
    for comp in sexpr.find_all(comps, "comp"):
        ref = sexpr.atom(comp, "ref")
        board.add_component(ComponentIR(
            ref=ref,
            value=sexpr.atom(comp, "value"),
            footprint=sexpr.atom(comp, "footprint"),
        ))

    # libparts carry pin number->name maps; libsource on each comp links them
    pin_maps: dict[tuple[str, str], dict[str, str]] = {}
    libparts = sexpr.find(root, "libparts") or []
    for part in sexpr.find_all(libparts, "libpart"):
        key = (sexpr.atom(part, "lib"), sexpr.atom(part, "part"))
        pins = {}
        pins_node = sexpr.find(part, "pins") or []
        for pin in sexpr.find_all(pins_node, "pin"):
            pins[sexpr.atom(pin, "num")] = sexpr.atom(pin, "name")
        pin_maps[key] = pins
    for comp in sexpr.find_all(comps, "comp"):
        src = sexpr.find(comp, "libsource")
        if src:
            key = (sexpr.atom(src, "lib"), sexpr.atom(src, "part"))
            board.components[sexpr.atom(comp, "ref")].pins = pin_maps.get(key, {})

    nets = sexpr.find(root, "nets") or []
    for net in sexpr.find_all(nets, "net"):
        net_name = sexpr.atom(net, "name")
        ir_net = NetIR(name=net_name, net_class=classify_net(net_name))
        for node in sexpr.find_all(net, "node"):
            ir_net.nodes.append(NetNode(ref=sexpr.atom(node, "ref"),
                                        pin=sexpr.atom(node, "pin")))
        board.add_net(ir_net)
    return board
