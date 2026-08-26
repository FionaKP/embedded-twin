"""BehaviorSpec: the intermediate representation between a datasheet and a
component model.

The extraction stage reads a datasheet and produces this structured spec;
the drafting stage turns the spec into model code + conformance tests. The
spec — not the generated code — is what a human reviews first, and its
`claims` are the datasheet facts the conformance tests must verify.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any

PIN_ROLES = {"power_in", "power_out", "gnd", "digital_in", "digital_out",
             "analog_in", "analog_out", "enable", "i2c_scl", "i2c_sda",
             "spi_sck", "spi_mosi", "spi_miso", "spi_cs",
             "uart_tx", "uart_rx", "nc", "other"}
FAMILIES = {"regulator", "sensor", "radio", "supervisor", "driver",
            "memory", "passive", "other"}
CLAIM_KINDS = {"current", "voltage", "register", "timing", "protocol", "state"}


@dataclass
class BehaviorSpec:
    mpn: str
    family: str
    description: str
    pins: list[dict[str, Any]] = field(default_factory=list)
    power_states: list[dict[str, Any]] = field(default_factory=list)
    interfaces: list[dict[str, Any]] = field(default_factory=list)
    behaviors: list[str] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    confidence_notes: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        problems = []
        if not self.mpn:
            problems.append("mpn is required")
        if self.family not in FAMILIES:
            problems.append(f"family {self.family!r} not in {sorted(FAMILIES)}")
        if not self.pins:
            problems.append("at least one pin is required")
        for p in self.pins:
            if not p.get("name"):
                problems.append(f"pin without name: {p}")
            if p.get("role") not in PIN_ROLES:
                problems.append(f"pin {p.get('name')}: bad role {p.get('role')!r}")
        for c in self.claims:
            if c.get("kind") not in CLAIM_KINDS:
                problems.append(f"claim {c.get('id')}: bad kind {c.get('kind')!r}")
            if not c.get("description"):
                problems.append(f"claim {c.get('id')}: description required")
        if not self.claims:
            problems.append("at least one testable claim is required "
                            "(a model without conformance claims is a hypothesis)")
        return problems

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @staticmethod
    def from_json(text: str) -> "BehaviorSpec":
        return BehaviorSpec(**json.loads(text))

    def slug(self) -> str:
        return "".join(ch.lower() if ch.isalnum() else "_"
                       for ch in self.mpn).strip("_")
