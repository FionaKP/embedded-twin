from .stm32f4 import build_stm32f4

PROFILES = {
    "stm32f4": build_stm32f4,
}

__all__ = ["PROFILES", "build_stm32f4"]
