"""The world the device lives in: where, when, and what RF conditions.

Attached to the kernel as ``kernel.env``; radio component models look it up.
All time conversion flows from one UTC start epoch + kernel nanoseconds, so
environment behavior is as deterministic as the rest of the sim.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..core import SimKernel
from ..core.kernel import SEC


@dataclass
class Position:
    lat: float
    lon: float
    alt_m: float = 0.0


class Environment:
    def __init__(self, kernel: SimKernel,
                 start_utc: datetime | str = "2026-08-25T12:00:00",
                 position: Position | None = None):
        self.kernel = kernel
        if isinstance(start_utc, str):
            start_utc = datetime.fromisoformat(start_utc)
        if start_utc.tzinfo is None:
            start_utc = start_utc.replace(tzinfo=timezone.utc)
        self.start_utc = start_utc
        self.position = position or Position(42.2626, -71.8023)  # Worcester, MA
        self.velocity_mps: tuple[float, float] = (0.0, 0.0)  # east, north
        self.gnss: Optional[object] = None       # GnssWorld
        self.cellular: Optional[object] = None   # CellularWorld
        self.ble: Optional[object] = None        # BleWorld
        kernel.env = self  # type: ignore[attr-defined]

    def utc_now(self) -> datetime:
        return self.start_utc + timedelta(seconds=self.kernel.now / SEC)

    def position_now(self) -> Position:
        """Static position plus simple linear motion if velocity is set."""
        ve, vn = self.velocity_mps
        if ve == 0 and vn == 0:
            return self.position
        t = self.kernel.now / SEC
        dlat = (vn * t) / 111_320.0
        import math
        dlon = (ve * t) / (111_320.0 * max(0.01, math.cos(math.radians(self.position.lat))))
        return Position(self.position.lat + dlat, self.position.lon + dlon,
                        self.position.alt_m)
