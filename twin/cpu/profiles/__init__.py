from .stm32f4 import build_stm32f4
from .nrf52 import build_nrf52

PROFILES = {
    "stm32f4": build_stm32f4,
    "nrf52": build_nrf52,
}

__all__ = ["PROFILES", "build_stm32f4", "build_nrf52"]
