"""Bind IR components to behavior model keys.

Priority: explicit `Model` column in the BOM > part-number table > refdes
heuristics. Anything left unbound is reported loudly — an unbound component
simulates as an open circuit, and the user must know that.

The part-number table is deliberately data (not code): this is the seam the
future datasheet-reading model factory writes into (ROADMAP v0.3).
"""
from __future__ import annotations

from ..ir import BoardIR

# MPN prefix -> model key. Checked longest-prefix-first.
PART_TABLE: dict[str, str] = {
    # regulators
    "AP2112": "regulator.ldo",
    "MCP1700": "regulator.ldo",
    "TLV757": "regulator.ldo",
    "TPS62": "regulator.buck",
    "MP2307": "regulator.buck",
    # GNSS receivers
    "NEO-M8": "gnss.generic_nmea",
    "NEO-M9": "gnss.generic_nmea",
    "MAX-M10": "gnss.generic_nmea",
    "L86": "gnss.generic_nmea",
    # cellular modems
    "SARA-R5": "cellular.at_modem",
    "SARA-U2": "cellular.at_modem",
    "BG95": "cellular.at_modem",
    "BG96": "cellular.at_modem",
    "SIM70": "cellular.at_modem",
    # BLE
    "RN4870": "ble.module",
    "BM71": "ble.module",
    # sensors
    "TMP102": "sensor.temp_i2c",
    "TMP117": "sensor.temp_i2c",
    "SHT3": "sensor.temp_i2c",
    # MCUs
    "STM32": "mcu.cortex_m",
    "ATSAMD": "mcu.cortex_m",
    "NRF52": "mcu.cortex_m",
}

_REFDES_TABLE: list[tuple[str, str]] = [
    ("LED", "passive.led"),
    ("SW", "input.button"),
    ("BT", "power.battery"),
    ("R", "passive.resistor"),
    ("C", "passive.capacitor"),
    ("L", "passive.inductor"),
    ("D", "passive.diode"),
    ("J", "connector.stub"),
    ("P", "connector.stub"),
    ("TP", "connector.stub"),
    ("U", ""),  # ICs must be identified, never guessed
]


def bind_models(board: BoardIR) -> dict:
    """Assign model keys in place. Returns a bind report."""
    report = {"explicit": [], "by_part_number": [], "by_refdes": [], "unbound": []}
    for comp in board.components.values():
        if comp.model:
            report["explicit"].append(comp.ref)
            continue
        mpn = comp.part_number.upper()
        if mpn:
            for prefix in sorted(PART_TABLE, key=len, reverse=True):
                if mpn.startswith(prefix):
                    comp.model = PART_TABLE[prefix]
                    report["by_part_number"].append(comp.ref)
                    break
        if comp.model:
            continue
        prefix = _refdes_prefix(comp.ref)
        for p, model in _REFDES_TABLE:
            if prefix == p and model:
                comp.model = model
                report["by_refdes"].append(comp.ref)
                break
        if not comp.model:
            report["unbound"].append(comp.ref)
    return report


def _refdes_prefix(ref: str) -> str:
    i = 0
    while i < len(ref) and not ref[i].isdigit():
        i += 1
    return ref[:i].upper()
