from .base import Component, register, REGISTRY
from . import passives, power_parts, inputs, sensors, mcu, radio  # noqa: F401  (registry population)

__all__ = ["Component", "register", "REGISTRY"]
