"""BOM CSV merge: enrich IR components with part numbers, params, model keys.

Recognized columns (case-insensitive): Reference/Refdes/Ref, Value,
PartNumber/MPN, Model, and any `param:<name>` columns which land in
``ComponentIR.params``.
"""
from __future__ import annotations

import csv
from pathlib import Path

from ..ir import BoardIR

_REF_COLS = {"reference", "refdes", "ref", "designator"}
_MPN_COLS = {"partnumber", "mpn", "part number", "manufacturer part number"}


def merge_bom(board: BoardIR, path: str | Path) -> list[str]:
    """Merge BOM rows into the board. Returns refdes present in BOM but not
    in the netlist (reported, never silently dropped)."""
    missing: list[str] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            cols = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            ref_col = next((c for c in cols if c in _REF_COLS), None)
            if not ref_col or not cols[ref_col]:
                continue
            # one row may cover several refs: "R1, R2, R3"
            for ref in [r.strip() for r in cols[ref_col].replace(";", ",").split(",") if r.strip()]:
                comp = board.components.get(ref)
                if comp is None:
                    missing.append(ref)
                    continue
                mpn_col = next((c for c in cols if c in _MPN_COLS), None)
                if mpn_col and cols[mpn_col]:
                    comp.part_number = cols[mpn_col]
                if cols.get("value"):
                    comp.value = cols["value"]
                if cols.get("model"):
                    comp.model = cols["model"]
                for k, v in cols.items():
                    if k.startswith("param:") and v:
                        comp.params[k[6:]] = _coerce(v)
    return missing


def _coerce(v: str):
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    return v
