"""Memory-mapped peripheral framework: address-table-driven, so a vendor
profile is data plus small register-behavior classes (FUTURE.md promise)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from ..components.mcu import CortexM


class Peripheral:
    """One register block. Subclasses override read/write by offset."""

    def __init__(self, name: str, base: int, size: int = 0x400):
        self.name = name
        self.base = base
        self.size = size
        self.regs: dict[int, int] = {}

    def read(self, offset: int, size: int) -> int:
        return self.regs.get(offset & ~3, 0)

    def write(self, offset: int, value: int, size: int) -> None:
        self.regs[offset & ~3] = value


class PeripheralBus:
    def __init__(self):
        self.periphs: list[Peripheral] = []

    def add(self, p: Peripheral) -> Peripheral:
        self.periphs.append(p)
        return p

    def find(self, addr: int) -> Optional[Peripheral]:
        for p in self.periphs:
            if p.base <= addr < p.base + p.size:
                return p
        return None

    def read(self, addr: int, size: int = 4) -> int:
        p = self.find(addr)
        return p.read(addr - p.base, size) if p else 0

    def write(self, addr: int, value: int, size: int = 4) -> None:
        p = self.find(addr)
        if p:
            p.write(addr - p.base, value, size)


# ---------------------------------------------------------------------------
# STM32-style peripheral behaviors
# ---------------------------------------------------------------------------

class RccStm32(Peripheral):
    """Clock control: whatever the firmware turns on reports ready
    immediately (HSIRDY/HSERDY/PLLRDY, SWS mirrors SW). Sim time does not
    model PLL lock time — that is microseconds of real hardware."""

    RESET = {0x00: 0x0000_0083}

    def __init__(self, base: int):
        super().__init__("RCC", base)
        self.regs.update(self.RESET)

    def read(self, offset: int, size: int) -> int:
        v = self.regs.get(offset & ~3, 0)
        off = offset & ~3
        if off == 0x00:  # CR: ready flags track enable bits
            for on_bit in (0, 16, 24, 26):   # HSI, HSE, PLL, PLLI2S
                if (v >> on_bit) & 1:
                    v |= 1 << (on_bit + 1)
        elif off == 0x08:  # CFGR: SWS = SW
            v = (v & ~0xC) | ((v & 0x3) << 2)
        return v


class GpioPortStm32(Peripheral):
    """MODER/IDR/ODR/BSRR-style port, wired to board nets via the MCU.

    Pins are named f"P{port}{bit}" (PA0..PA15 …). Output-mode pins drive
    their nets; input reads sample them. AF/analog modes release the net
    (AF signals — UART etc. — are driven by their own peripheral models).
    """

    MODER, OTYPER, IDR, ODR, BSRR = 0x00, 0x04, 0x10, 0x14, 0x18

    def __init__(self, base: int, letter: str, mcu: "CortexM"):
        super().__init__(f"GPIO{letter}", base)
        self.letter = letter
        self.mcu = mcu
        self.moder = 0
        self.odr = 0

    def pin_name(self, bit: int) -> str:
        return f"P{self.letter}{bit}"

    def read(self, offset: int, size: int) -> int:
        off = offset & ~3
        if off == self.IDR:
            word = 0
            for bit in range(16):
                if self.mcu.gpio_read(self.pin_name(bit)):
                    word |= 1 << bit
            return word
        if off == self.ODR:
            return self.odr
        if off == self.MODER:
            return self.moder
        return super().read(offset, size)

    def write(self, offset: int, value: int, size: int) -> None:
        off = offset & ~3
        if off == self.MODER:
            changed = self.moder ^ value
            self.moder = value
            for bit in range(16):
                if (changed >> (2 * bit)) & 3:
                    self._apply(bit)
        elif off == self.ODR:
            changed = self.odr ^ value
            self.odr = value
            for bit in range(16):
                if (changed >> bit) & 1:
                    self._apply(bit)
        elif off == self.BSRR:
            odr = self.odr
            odr |= value & 0xFFFF
            odr &= ~((value >> 16) & 0xFFFF)
            self.write(self.ODR, odr, 4)
        else:
            super().write(offset, value, size)

    def _apply(self, bit: int) -> None:
        mode = (self.moder >> (2 * bit)) & 3
        name = self.pin_name(bit)
        if self.mcu.net(name) is None:
            return
        if mode == 1:  # general-purpose output
            self.mcu.gpio_drive(name, (self.odr >> bit) & 1)
        else:          # input / AF / analog: this block does not drive
            self.mcu.gpio_release(name)


class UsartStm32(Peripheral):
    """SR/DR-style USART bound to a board-level Uart port.

    Baud comes from the board wiring (Uart object), not BRR — the sim does
    not model clock trees. RXNEIE pends the NVIC IRQ on received bytes.
    """

    SR, DR, BRR, CR1 = 0x00, 0x04, 0x08, 0x0C

    def __init__(self, name: str, base: int, mcu: "CortexM", irq: int):
        super().__init__(name, base)
        self.mcu = mcu
        self.irq = irq
        self.rx_fifo: list[int] = []
        self.port = None  # bound later by the profile if wired

    def on_rx(self, b: int) -> None:
        self.rx_fifo.append(b)
        if self.regs.get(self.CR1, 0) & (1 << 5):  # RXNEIE
            self.mcu.pend_irq(self.irq)

    def read(self, offset: int, size: int) -> int:
        off = offset & ~3
        if off == self.SR:
            v = (1 << 7) | (1 << 6)          # TXE | TC always ready
            if self.rx_fifo:
                v |= 1 << 5                   # RXNE
            return v
        if off == self.DR:
            return self.rx_fifo.pop(0) if self.rx_fifo else 0
        return super().read(offset, size)

    def write(self, offset: int, value: int, size: int) -> None:
        off = offset & ~3
        if off == self.DR:
            if self.port is not None:
                self.port.send(bytes([value & 0xFF]))
        else:
            super().write(offset, value, size)


class PwrStm32(Peripheral):
    def read(self, offset: int, size: int) -> int:
        if (offset & ~3) == 0x04:            # CSR: VOSRDY
            return super().read(offset, size) | (1 << 14)
        return super().read(offset, size)
