"""STM32F4 memory-map profile: register-level firmware (CMSIS or HAL style)
runs against real STM32F405/407-family addresses.

Covered: RCC (auto-ready clocks), FLASH ACR, PWR, GPIOA–GPIOE, USART1/2/6,
SysTick/NVIC/SCB (via the ARMv7-M system layer). Anything else in the
peripheral window reads 0 / swallows writes — extend by adding blocks here.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..periph import (AdcStm32, ExtiStm32, GpioPortStm32, I2cStm32,
                      Peripheral, PeripheralBus, PwrStm32, RccStm32,
                      SpiStm32, TimStm32, UsartStm32)
from .base import Profile

if TYPE_CHECKING:
    from ...components.mcu import CortexM

FLASH_BASE = 0x0800_0000
FLASH_SIZE = 1024 * 1024
RAM_BASE = 0x2000_0000
RAM_SIZE = 128 * 1024
CCM_BASE = 0x1000_0000
CCM_SIZE = 64 * 1024
PERIPH_BASE = 0x4000_0000
PERIPH_SIZE = 0x1000_0000

USART_IRQS = {"USART1": 37, "USART2": 38, "USART6": 71}
USART_BASES = {"USART1": 0x4001_1000, "USART2": 0x4000_4400, "USART6": 0x4001_1400}


def build_stm32f4(mcu: "CortexM") -> Profile:
    bus = PeripheralBus()
    bus.add(RccStm32(0x4002_3800))
    bus.add(Peripheral("FLASH_IF", 0x4002_3C00))
    bus.add(PwrStm32("PWR", 0x4000_7000))

    ports = {}
    for i, letter in enumerate("ABCDE"):
        ports[letter] = bus.add(GpioPortStm32(0x4002_0000 + 0x400 * i, letter, mcu))

    usarts = {}
    for name, base in USART_BASES.items():
        usarts[name] = bus.add(UsartStm32(name, base, mcu, USART_IRQS[name]))

    named = {}
    for name, base, irq in (("SPI1", 0x4001_3000, 35), ("SPI2", 0x4000_3800, 36),
                            ("SPI3", 0x4000_3C00, 51)):
        named[name] = bus.add(SpiStm32(name, base, mcu, irq))
    for name, base, irq in (("I2C1", 0x4000_5400, 31), ("I2C2", 0x4000_5800, 33)):
        named[name] = bus.add(I2cStm32(name, base, mcu, irq))
    named["ADC1"] = bus.add(AdcStm32("ADC1", 0x4001_2000, mcu))
    for name, base, irq in (("TIM2", 0x4000_0000, 28), ("TIM3", 0x4000_0400, 29),
                            ("TIM4", 0x4000_0800, 30)):
        named[name] = bus.add(TimStm32(name, base, mcu, irq))
    syscfg = bus.add(Peripheral("SYSCFG", 0x4001_3800))
    named["EXTI"] = bus.add(ExtiStm32("EXTI", 0x4001_3C00, mcu, syscfg))

    return Profile(
        name="stm32f4",
        flash_base=FLASH_BASE, flash_size=FLASH_SIZE,
        ram_regions=[(RAM_BASE, RAM_SIZE), (CCM_BASE, CCM_SIZE)],
        periph_base=PERIPH_BASE, periph_size=PERIPH_SIZE,
        bus=bus, usarts=usarts, gpio_ports=ports, named=named,
    )
