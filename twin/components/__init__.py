from .base import Component, register, REGISTRY
from . import passives, power_parts, inputs, sensors  # noqa: F401  (registry population)

__all__ = ["Component", "register", "REGISTRY"]
