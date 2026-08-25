"""Cellular world: towers, path loss, network load, kick-offs.

Path loss: urban macro log-distance (128.1 + 37.6·log10(d_km)) plus a
deterministic per-tower shadowing term. Network load is scenario-controlled
over time; when load exceeds the kick threshold the tower sheds attached
devices (a network-initiated detach — the "getting kicked off" case).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from .world import Position


@dataclass
class Tower:
    id: str
    lat: float
    lon: float
    tx_power_dbm: float = 43.0
    load: float = 0.2            # 0..1
    kick_threshold: float = 0.9
    reject_threshold: float = 0.85
    shadowing_db: float = 0.0


def _distance_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    dlat = math.radians(b_lat - a_lat)
    dlon = math.radians(b_lon - a_lon)
    x = dlon * math.cos(math.radians((a_lat + b_lat) / 2))
    return 6371.0 * math.hypot(dlat, x)


class CellularWorld:
    def __init__(self, towers: list[Tower] | None = None):
        self.towers = towers or [Tower(id="cell-A", lat=42.27, lon=-71.81)]
        self._listeners: list[Callable[[Tower], None]] = []

    def on_load_change(self, fn: Callable[[Tower], None]) -> None:
        self._listeners.append(fn)

    def set_load(self, tower_id: str, load: float) -> None:
        for t in self.towers:
            if t.id == tower_id:
                t.load = load
                for fn in self._listeners:
                    fn(t)

    def rssi_dbm(self, tower: Tower, pos: Position) -> float:
        d_km = max(0.01, _distance_km(tower.lat, tower.lon, pos.lat, pos.lon))
        path_loss = 128.1 + 37.6 * math.log10(d_km)
        return tower.tx_power_dbm - path_loss - tower.shadowing_db

    def best_tower(self, pos: Position) -> tuple[Tower, float] | None:
        best = None
        for t in self.towers:
            rssi = self.rssi_dbm(t, pos)
            if best is None or rssi > best[1]:
                best = (t, rssi)
        return best

    @staticmethod
    def csq(rssi_dbm: float) -> int:
        """3GPP TS 27.007 +CSQ scale: 0 (<=-113 dBm) .. 31 (>=-51 dBm)."""
        return max(0, min(31, int((rssi_dbm + 113) / 2)))
