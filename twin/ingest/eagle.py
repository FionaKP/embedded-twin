"""Eagle schematic (.sch XML) -> Board IR.

Eagle schematics carry parts and nets (with pin *names*, not numbers) in one
XML file — no separate netlist export needed. Supply symbols (GND/3V3/...)
are schematic sugar, not components: they are dropped, and the nets they
touch keep their names.

This frontend unlocks the large Adafruit/SparkFun-era open-hardware corpus.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ..ir import BoardIR, ComponentIR, NetIR, NetNode
from .kicad import classify_net as _kicad_classify

_SUPPLY_DEVICESETS = {
    "GND", "AGND", "DGND", "PGND", "VCC", "VDD", "VSS", "V+", "V-",
    "3V3", "+3V3", "3.3V", "5V", "+5V", "VBAT", "VBUS", "VUSB", "VIN",
}
_RAIL_NAME = re.compile(r"^\+?\d+(\.\d+)?V$", re.IGNORECASE)
_POWER_NAMES = {"BAT", "VBAT", "USB", "VUSB", "VBUS", "VIN", "VHI", "EN"}


def classify_net(name: str) -> str:
    base = name.upper()
    if _RAIL_NAME.match(base) or base in _POWER_NAMES - {"EN"}:
        return "power"
    return _kicad_classify(name)


def _is_supply(library: str, deviceset: str) -> bool:
    return (library or "").lower().startswith("supply") or \
        (deviceset or "").upper() in _SUPPLY_DEVICESETS


def parse_eagle_sch(path: str | Path, name: str | None = None) -> BoardIR:
    path = Path(path)
    root = ET.parse(path).getroot()
    sch = root.find(".//schematic")
    if sch is None:
        raise ValueError(f"{path}: not an Eagle schematic")

    board = BoardIR(name=name or path.stem,
                    metadata={"source": str(path), "format": "eagle-sch"})

    supplies: set[str] = set()
    for part in sch.find("parts") or []:
        ref = part.get("name")
        lib, devset = part.get("library", ""), part.get("deviceset", "")
        if _is_supply(lib, devset):
            supplies.add(ref)
            continue
        board.add_component(ComponentIR(
            ref=ref,
            value=part.get("value") or devset,
            footprint=part.get("device", ""),
            params={"eagle_deviceset": devset} if devset else {},
        ))

    # nets can span sheets; merge by name
    for sheet in sch.find("sheets") or []:
        for net in sheet.find("nets") or []:
            net_name = net.get("name")
            ir_net = board.nets.get(net_name)
            if ir_net is None:
                ir_net = board.add_net(NetIR(name=net_name,
                                             net_class=classify_net(net_name)))
            for seg in net:
                for pinref in seg.findall("pinref"):
                    ref, pin = pinref.get("part"), pinref.get("pin")
                    if ref in supplies:
                        continue
                    comp = board.components.get(ref)
                    if comp is None:
                        continue
                    comp.pins.setdefault(pin, pin)  # Eagle pins are names
                    node = NetNode(ref=ref, pin=pin)
                    if not any(n.ref == ref and n.pin == pin
                               for n in ir_net.nodes):
                        ir_net.nodes.append(node)
    return board
