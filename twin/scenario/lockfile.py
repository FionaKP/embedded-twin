"""Run lockfile (ADR-0001): pin every input to a content hash."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .. import __version__
from ..ir import BoardIR
from .spec import Scenario


def _file_hash(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def make_lock(scenario: Scenario, board: BoardIR) -> dict:
    cfg = scenario.raw.get("board", {})
    inputs: dict[str, str | None] = {}
    for key in ("netlist", "bom", "ir"):
        if cfg.get(key):
            inputs[key] = _file_hash(scenario.resolve(cfg[key]))
    for ref, spec in (scenario.raw.get("firmware", {}) or {}).items():
        if "file" in spec:
            inputs[f"firmware:{ref}"] = _file_hash(scenario.resolve(spec["file"]))
        elif "c" in spec:
            inputs[f"firmware:{ref}"] = _file_hash(scenario.resolve(spec["c"]))
        elif "behavioral" in spec:
            inputs[f"firmware:{ref}"] = f"behavioral:{spec['behavioral']}"
    lock = {
        "twin_version": __version__,
        "scenario": _file_hash(scenario.path),
        "board_ir": board.sha256(),
        "inputs": inputs,
        "seed": scenario.seed,
    }
    lock["lock_hash"] = hashlib.sha256(
        json.dumps(lock, sort_keys=True).encode()).hexdigest()[:16]
    return lock
