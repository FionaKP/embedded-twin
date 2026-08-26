"""Shared vendor-profile shape."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..periph import PeripheralBus


@dataclass
class Profile:
    name: str
    flash_base: int
    flash_size: int
    ram_regions: list[tuple[int, int]]
    periph_base: int
    periph_size: int
    bus: PeripheralBus
    usarts: dict = field(default_factory=dict)       # name -> uart-capable periph
    gpio_ports: dict = field(default_factory=dict)
