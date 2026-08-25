"""Scenario file loading and time parsing."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..core.kernel import MS, SEC, US

_UNITS = {"h": 3600 * SEC, "m": 60 * SEC, "s": SEC, "ms": MS, "us": US}


def parse_time(v) -> int:
    """'24h' | '90s' | '500ms' | number-of-seconds -> nanoseconds."""
    if isinstance(v, (int, float)):
        return int(v * SEC)
    s = str(v).strip()
    for suffix in ("ms", "us", "h", "m", "s"):
        if s.endswith(suffix):
            return int(float(s[: -len(suffix)]) * _UNITS[suffix])
    return int(float(s) * SEC)


@dataclass
class Scenario:
    name: str
    path: Path
    raw: dict[str, Any]
    duration_ns: int
    seed: int
    events: list[dict] = field(default_factory=list)
    assertions: list[dict] = field(default_factory=list)

    @property
    def dir(self) -> Path:
        return self.path.parent

    def resolve(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else (self.dir / p)


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    return Scenario(
        name=raw.get("name", path.stem),
        path=path,
        raw=raw,
        duration_ns=parse_time(raw.get("duration", "10s")),
        seed=int(raw.get("seed", 0)),
        events=raw.get("events", []) or [],
        assertions=raw.get("assertions", []) or [],
    )
