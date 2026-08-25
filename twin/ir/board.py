"""Board IR: the canonical, versionable representation of a board.

Structure only — components, their pins, and the nets tying them together.
Behavior comes from binding each component's ``model`` key to a class in the
model registry at build time. The IR is plain-JSON serializable so it can be
diffed, hashed (ADR-0001), and produced by any ingest frontend.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class NetNode:
    ref: str   # component refdes
    pin: str   # pin number (as printed on the part)


@dataclass
class NetIR:
    name: str
    net_class: str = "signal"  # signal | power | ground
    nodes: list[NetNode] = field(default_factory=list)


@dataclass
class ComponentIR:
    ref: str                      # refdes: U1, R5 ...
    value: str = ""               # 10k, STM32F405, ...
    footprint: str = ""
    part_number: str = ""         # MPN from BOM
    model: str = ""               # model registry key; "" = unbound
    params: dict[str, Any] = field(default_factory=dict)
    pins: dict[str, str] = field(default_factory=dict)  # pin number -> pin name


@dataclass
class BoardIR:
    name: str
    components: dict[str, ComponentIR] = field(default_factory=dict)
    nets: dict[str, NetIR] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- construction -----------------------------------------------------
    def add_component(self, comp: ComponentIR) -> ComponentIR:
        if comp.ref in self.components:
            raise ValueError(f"duplicate refdes {comp.ref}")
        self.components[comp.ref] = comp
        return comp

    def add_net(self, net: NetIR) -> NetIR:
        if net.name in self.nets:
            raise ValueError(f"duplicate net {net.name}")
        self.nets[net.name] = net
        return net

    def nets_of(self, ref: str) -> dict[str, str]:
        """pin number -> net name for one component."""
        out: dict[str, str] = {}
        for net in self.nets.values():
            for node in net.nodes:
                if node.ref == ref:
                    out[node.pin] = net.name
        return out

    def unbound(self) -> list[str]:
        return [c.ref for c in self.components.values() if not c.model]

    # -- serialization ----------------------------------------------------
    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @staticmethod
    def from_json(text: str) -> "BoardIR":
        raw = json.loads(text)
        board = BoardIR(name=raw["name"], metadata=raw.get("metadata", {}))
        for ref, c in raw.get("components", {}).items():
            board.components[ref] = ComponentIR(**c)
        for name, n in raw.get("nets", {}).items():
            board.nets[name] = NetIR(name=n["name"], net_class=n["net_class"],
                                     nodes=[NetNode(**nd) for nd in n["nodes"]])
        return board

    def sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()
