"""BLE world: link-level conditions for BLE modules on the board.

Interference (0..1) drives per-connection-event packet loss; a run of lost
connection events longer than the supervision timeout drops the link —
the classic "my BLE device randomly disconnects" case, reproducible.
"""
from __future__ import annotations


class BleWorld:
    def __init__(self, interference: float = 0.0, range_ok: bool = True):
        self.interference = interference   # 0 = clean, 1 = unusable
        self.range_ok = range_ok           # False = peer out of range

    def packet_loss_probability(self) -> float:
        if not self.range_ok:
            return 1.0
        # gentle at low interference, brutal near 1
        return min(1.0, self.interference ** 1.5)
