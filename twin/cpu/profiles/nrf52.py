"""nRF52 memory-map profile (nRF52832-class): task/event peripherals.

Covered: CLOCK (task-started events), GPIO P0 (OUT/OUTSET/OUTCLR/IN/
DIR/DIRSET/PIN_CNF), UARTE0 with EasyDMA (STARTTX/STARTRX against real RAM
buffers, ENDTX/ENDRX/RXDRDY events, INTENSET-driven interrupts), plus the
ARMv7-M core layer (SysTick/NVIC/SCB) shared with every profile.

Flash lives at 0x0000_0000 on nRF52 — the vector table is the image start.
Pins are named P0.x (netlist pin names "P0.13", "P0_13", or "P013" all
match). IRQ number = peripheral ID = (base - 0x4000_0000) >> 12.
"""
from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from ..periph import Peripheral, PeripheralBus
from .base import Profile

if TYPE_CHECKING:
    from ...components.mcu import CortexM

FLASH_BASE = 0x0000_0000
FLASH_SIZE = 512 * 1024
RAM_BASE = 0x2000_0000
RAM_SIZE = 64 * 1024
# one window covering 0x4000_0000 peripherals AND GPIO at 0x5000_0000
PERIPH_BASE = 0x4000_0000
PERIPH_SIZE = 0x1000_1000

UARTE0_IRQ = 2


class ClockNrf(Peripheral):
    """TASKS_HFCLKSTART/LFCLKSTART -> corresponding STARTED events set."""

    def write(self, offset: int, value: int, size: int) -> None:
        off = offset & ~3
        if off == 0x000 and value:      # TASKS_HFCLKSTART
            self.regs[0x100] = 1        # EVENTS_HFCLKSTARTED
        elif off == 0x008 and value:    # TASKS_LFCLKSTART
            self.regs[0x104] = 1        # EVENTS_LFCLKSTARTED
        else:
            super().write(offset, value, size)


class GpioNrf(Peripheral):
    OUT, OUTSET, OUTCLR = 0x504, 0x508, 0x50C
    IN, DIR, DIRSET, DIRCLR = 0x510, 0x514, 0x518, 0x51C
    PIN_CNF0 = 0x700

    def __init__(self, base: int, mcu: "CortexM"):
        super().__init__("P0", base, size=0x1000)
        self.mcu = mcu
        self.out = 0
        self.dir = 0

    def _pin_net_names(self, bit: int) -> tuple[str, ...]:
        return (f"P0.{bit}", f"P0.{bit:02d}", f"P0_{bit}", f"P0{bit:02d}")

    def read(self, offset: int, size: int) -> int:
        off = offset & ~3
        if off == self.OUT:
            return self.out
        if off == self.DIR:
            return self.dir
        if off == self.IN:
            word = 0
            for bit in range(32):
                net = self.mcu.net(*self._pin_net_names(bit))
                if net is not None and net.is_high:
                    word |= 1 << bit
            return word
        if self.PIN_CNF0 <= off < self.PIN_CNF0 + 32 * 4:
            bit = (off - self.PIN_CNF0) // 4
            return self.regs.get(off, 0x2) | ((self.dir >> bit) & 1)
        return super().read(offset, size)

    def write(self, offset: int, value: int, size: int) -> None:
        off = offset & ~3
        if off == self.OUT:
            self._update(out=value)
        elif off == self.OUTSET:
            self._update(out=self.out | value)
        elif off == self.OUTCLR:
            self._update(out=self.out & ~value)
        elif off == self.DIR:
            self._update(dir_=value)
        elif off == self.DIRSET:
            self._update(dir_=self.dir | value)
        elif off == self.DIRCLR:
            self._update(dir_=self.dir & ~value)
        elif self.PIN_CNF0 <= off < self.PIN_CNF0 + 32 * 4:
            self.regs[off] = value
            bit = (off - self.PIN_CNF0) // 4
            if value & 1:
                self._update(dir_=self.dir | (1 << bit))
            else:
                self._update(dir_=self.dir & ~(1 << bit))
        else:
            super().write(offset, value, size)

    def _update(self, out: int | None = None, dir_: int | None = None) -> None:
        old_out, old_dir = self.out, self.dir
        if out is not None:
            self.out = out
        if dir_ is not None:
            self.dir = dir_
        changed = (old_out ^ self.out) | (old_dir ^ self.dir)
        from ...core import Drive
        for bit in range(32):
            if not (changed >> bit) & 1:
                continue
            net = self.mcu.net(*self._pin_net_names(bit))
            if net is None:
                continue
            drv_id = f"{self.mcu.ref}.P0.{bit}"
            if (self.dir >> bit) & 1:
                net.drive(drv_id, Drive.high(self.mcu.vdd_v)
                          if (self.out >> bit) & 1 else Drive.low())
            else:
                net.drive(drv_id, Drive.release())


class UarteNrf(Peripheral):
    """UARTE with EasyDMA against emulated RAM.

    TX: TASKS_STARTTX reads TXD.PTR/MAXCNT from RAM, sends, sets ENDTX.
    RX: TASKS_STARTRX arms reception into RXD.PTR; each received byte sets
    RXDRDY, and filling MAXCNT (or STOPRX) sets ENDRX with AMOUNT.
    """

    T_STARTRX, T_STOPRX, T_STARTTX, T_STOPTX = 0x000, 0x004, 0x008, 0x00C
    E_RXDRDY, E_ENDRX, E_TXDRDY, E_ENDTX = 0x108, 0x110, 0x11C, 0x120
    INTENSET, INTENCLR, ENABLE = 0x304, 0x308, 0x500
    RXD_PTR, RXD_MAXCNT, RXD_AMOUNT = 0x534, 0x538, 0x53C
    TXD_PTR, TXD_MAXCNT, TXD_AMOUNT = 0x544, 0x548, 0x54C

    _INT_BITS = {E_RXDRDY: 2, E_ENDRX: 4, E_TXDRDY: 7, E_ENDTX: 8}

    def __init__(self, name: str, base: int, mcu: "CortexM", irq: int):
        super().__init__(name, base, size=0x1000)
        self.mcu = mcu
        self.irq = irq
        self.port = None
        self.inten = 0
        self.rx_armed = False
        self.rx_count = 0

    # -- helpers ----------------------------------------------------------
    def _event(self, off: int) -> None:
        self.regs[off] = 1
        bit = self._INT_BITS.get(off)
        if bit is not None and (self.inten >> bit) & 1:
            self.mcu.pend_irq(self.irq)

    def _mem(self):
        return self.mcu.backend.uc

    # -- UART side (board wiring) -----------------------------------------
    def on_rx(self, b: int) -> None:
        if not self.rx_armed:
            return
        ptr = self.regs.get(self.RXD_PTR, 0)
        maxcnt = self.regs.get(self.RXD_MAXCNT, 0)
        if self.rx_count < maxcnt:
            self._mem().mem_write(ptr + self.rx_count, bytes([b & 0xFF]))
            self.rx_count += 1
            self._event(self.E_RXDRDY)
        if self.rx_count >= maxcnt:
            self.regs[self.RXD_AMOUNT] = self.rx_count
            self.rx_armed = False
            self._event(self.E_ENDRX)

    # -- register interface ------------------------------------------------
    def read(self, offset: int, size: int) -> int:
        off = offset & ~3
        if off == self.INTENSET or off == self.INTENCLR:
            return self.inten
        return super().read(offset, size)

    def write(self, offset: int, value: int, size: int) -> None:
        off = offset & ~3
        if off == self.T_STARTTX and value:
            ptr = self.regs.get(self.TXD_PTR, 0)
            maxcnt = self.regs.get(self.TXD_MAXCNT, 0)
            if self.port is not None and maxcnt:
                data = bytes(self._mem().mem_read(ptr, maxcnt))
                self.port.send(data)
            self.regs[self.TXD_AMOUNT] = maxcnt
            self._event(self.E_TXDRDY)
            self._event(self.E_ENDTX)
        elif off == self.T_STARTRX and value:
            self.rx_armed = True
            self.rx_count = 0
        elif off == self.T_STOPRX and value:
            if self.rx_armed:
                self.regs[self.RXD_AMOUNT] = self.rx_count
                self.rx_armed = False
                self._event(self.E_ENDRX)
        elif off == self.INTENSET:
            self.inten |= value
        elif off == self.INTENCLR:
            self.inten &= ~value
        else:
            super().write(offset, value, size)


def build_nrf52(mcu: "CortexM") -> Profile:
    bus = PeripheralBus()
    bus.add(ClockNrf("CLOCK", 0x4000_0000, size=0x1000))
    uarte0 = bus.add(UarteNrf("UARTE0", 0x4000_2000, mcu, UARTE0_IRQ))
    p0 = bus.add(GpioNrf(0x5000_0000, mcu))
    return Profile(
        name="nrf52",
        flash_base=FLASH_BASE, flash_size=FLASH_SIZE,
        ram_regions=[(RAM_BASE, RAM_SIZE)],
        periph_base=PERIPH_BASE, periph_size=PERIPH_SIZE,
        bus=bus, usarts={"UARTE0": uarte0}, gpio_ports={"P0": p0},
    )
