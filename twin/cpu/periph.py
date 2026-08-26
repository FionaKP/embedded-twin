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


class SpiStm32(Peripheral):
    """CR1/SR/DR-style SPI master bound to a board-level SpiBus."""

    CR1, SR, DR = 0x00, 0x08, 0x0C

    def __init__(self, name: str, base: int, mcu: "CortexM", irq: int):
        super().__init__(name, base)
        self.mcu = mcu
        self.irq = irq
        self.bus_ref = None            # SpiBus, bound by the MCU from params
        self.rx: list[int] = []

    def read(self, offset: int, size: int) -> int:
        off = offset & ~3
        if off == self.SR:
            v = 1 << 1                 # TXE always
            if self.rx:
                v |= 1                 # RXNE
            return v
        if off == self.DR:
            return self.rx.pop(0) if self.rx else 0
        return super().read(offset, size)

    def write(self, offset: int, value: int, size: int) -> None:
        off = offset & ~3
        if off == self.DR:
            if self.bus_ref is not None:
                self.rx.append(self.bus_ref.transfer(value & 0xFF))
        else:
            super().write(offset, value, size)


class I2cStm32(Peripheral):
    """STM32F4 I2C master, modeled at the level polled HAL drivers use:
    START->SB, address->ADDR (or AF nack), TXE/BTF per byte, STOP commits
    the transfer to the board-level I2CBus. Reads prefetch from the device
    at address phase and stream out of DR."""

    CR1, DR, SR1, SR2 = 0x00, 0x10, 0x14, 0x18
    SB, ADDR, BTF, RXNE, TXE, AF = 1 << 0, 1 << 1, 1 << 2, 1 << 6, 1 << 7, 1 << 10

    def __init__(self, name: str, base: int, mcu: "CortexM", irq: int):
        super().__init__(name, base)
        self.mcu = mcu
        self.irq = irq
        self.bus = None                # comm.I2CBus, bound by the MCU
        self.sr1 = 0
        self.addr = None
        self.reading = False
        self.tx: list[int] = []
        self.rx: list[int] = []

    def read(self, offset: int, size: int) -> int:
        off = offset & ~3
        if off == self.SR1:
            return self.sr1
        if off == self.SR2:
            self.sr1 &= ~self.ADDR     # SR1-then-SR2 read clears ADDR
            return 0x2                 # BUSY
        if off == self.DR:
            b = self.rx.pop(0) if self.rx else 0
            if not self.rx:
                self.sr1 &= ~self.RXNE
            return b
        return super().read(offset, size)

    def write(self, offset: int, value: int, size: int) -> None:
        off = offset & ~3
        if off == self.CR1:
            if value & (1 << 8):       # START
                self.sr1 = self.SB
                self.tx, self.rx = [], []
                self.addr = None
            if value & (1 << 9):       # STOP: commit a pending write
                if self.addr is not None and not self.reading and self.bus:
                    ok = self.bus.write(self.addr, bytes(self.tx))
                    if not ok:
                        self.sr1 |= self.AF
                self.addr = None
            self.regs[off] = value & ~0x300
        elif off == self.DR:
            if self.sr1 & self.SB:     # address phase
                self.sr1 &= ~self.SB
                self.addr = (value >> 1) & 0x7F
                self.reading = bool(value & 1)
                dev = self.bus.devices.get(self.addr) if self.bus else None
                if dev is None:
                    self.sr1 |= self.AF
                    self.addr = None
                    return
                self.sr1 |= self.ADDR
                if self.reading:
                    data = self.bus.read(self.addr, 4) or b""
                    self.rx = list(data)
                    if self.rx:
                        self.sr1 |= self.RXNE
                else:
                    self.sr1 |= self.TXE
            elif self.addr is not None and not self.reading:
                self.tx.append(value & 0xFF)
                self.sr1 |= self.TXE | self.BTF
        else:
            super().write(offset, value, size)


class AdcStm32(Peripheral):
    """12-bit ADC sampling board net voltages. Channels map to pins the
    STM32F4 way (ch0-7 = PA0-7, ch8-9 = PB0-1, ch10-15 = PC0-5).
    params on the MCU: adc_vref (3.3)."""

    SR, CR2, SQR3, DR = 0x00, 0x08, 0x34, 0x4C
    CHANNEL_PINS = {**{i: f"PA{i}" for i in range(8)},
                    8: "PB0", 9: "PB1",
                    **{10 + i: f"PC{i}" for i in range(6)}}

    def __init__(self, name: str, base: int, mcu: "CortexM"):
        super().__init__(name, base)
        self.mcu = mcu
        self.eoc = False
        self.result = 0

    def _sample(self) -> None:
        chan = self.regs.get(self.SQR3, 0) & 0x1F
        net = self.mcu.net(self.CHANNEL_PINS.get(chan, ""))
        vref = float(self.mcu.params.get("adc_vref", 3.3))
        v = 0.0
        if net is not None:
            v = net.voltage if net.voltage is not None else (vref if net.is_high else 0.0)
        self.result = max(0, min(4095, int(round(v / vref * 4095))))
        self.eoc = True

    def read(self, offset: int, size: int) -> int:
        off = offset & ~3
        if off == self.SR:
            return (1 << 1) if self.eoc else 0
        if off == self.DR:
            self.eoc = False
            return self.result
        return super().read(offset, size)

    def write(self, offset: int, value: int, size: int) -> None:
        off = offset & ~3
        if off == self.CR2:
            self.regs[off] = value
            if value & (1 << 30):      # SWSTART
                self._sample()
        else:
            super().write(offset, value, size)


class TimStm32(Peripheral):
    """Basic up-counting timer derived from kernel time: CR1.CEN, PSC, ARR,
    CNT, SR.UIF, DIER.UIE -> update-event interrupts (catch-up, one pend per
    slice like SysTick)."""

    CR1, DIER, SR, CNT, PSC, ARR = 0x00, 0x0C, 0x10, 0x24, 0x28, 0x2C

    def __init__(self, name: str, base: int, mcu: "CortexM", irq: int):
        super().__init__(name, base)
        self.mcu = mcu
        self.irq = irq
        self.t0 = 0
        self.updates_seen = 0
        self.uif = False

    def _period_ns(self) -> int:
        psc = self.regs.get(self.PSC, 0) + 1
        arr = self.regs.get(self.ARR, 0) + 1
        return max(1, int(psc * arr * 1_000_000_000 / self.mcu.clock_hz))

    def _enabled(self) -> bool:
        return bool(self.regs.get(self.CR1, 0) & 1)

    def advance(self, now: int) -> None:
        if not self._enabled():
            return
        updates = (now - self.t0) // self._period_ns()
        if updates > self.updates_seen:
            self.updates_seen = updates
            self.uif = True
            if self.regs.get(self.DIER, 0) & 1:
                self.mcu.pend_irq(self.irq)

    def next_event_ns(self, now: int):
        if not (self._enabled() and self.regs.get(self.DIER, 0) & 1):
            return None
        return self.t0 + (self.updates_seen + 1) * self._period_ns()

    def read(self, offset: int, size: int) -> int:
        off = offset & ~3
        if off == self.SR:
            return 1 if self.uif else 0
        if off == self.CNT:
            if not self._enabled():
                return 0
            psc = self.regs.get(self.PSC, 0) + 1
            arr = self.regs.get(self.ARR, 0) + 1
            ticks = (self.mcu.kernel.now - self.t0) * self.mcu.clock_hz \
                // 1_000_000_000 // psc
            return int(ticks % arr)
        return super().read(offset, size)

    def write(self, offset: int, value: int, size: int) -> None:
        off = offset & ~3
        if off == self.CR1:
            was = self._enabled()
            self.regs[off] = value
            if self._enabled() and not was:
                self.t0 = self.mcu.kernel.now
                self.updates_seen = 0
        elif off == self.SR:
            if not (value & 1):        # write 0 clears UIF
                self.uif = False
        else:
            super().write(offset, value, size)


class ExtiStm32(Peripheral):
    """External interrupt lines: net-edge -> pending -> NVIC. Lines bind to
    pins via SYSCFG.EXTICR (port letter) lazily on IMR/EXTICR writes."""

    IMR, EMR, RTSR, FTSR, SWIER, PR = 0x00, 0x04, 0x08, 0x0C, 0x10, 0x14
    LINE_IRQ = {0: 6, 1: 7, 2: 8, 3: 9, 4: 10,
                **{n: 23 for n in range(5, 10)},
                **{n: 40 for n in range(10, 16)}}

    def __init__(self, name: str, base: int, mcu: "CortexM", syscfg: Peripheral):
        super().__init__(name, base)
        self.mcu = mcu
        self.syscfg = syscfg
        self.pr = 0
        self._subscribed: dict[int, str] = {}   # line -> pin name
        self._level: dict[int, bool] = {}

    def _port_of(self, line: int) -> str:
        word = self.syscfg.regs.get(0x08 + 4 * (line // 4), 0)
        sel = (word >> (4 * (line % 4))) & 0xF
        return "ABCDE"[sel] if sel < 5 else "A"

    def _resubscribe(self) -> None:
        imr = self.regs.get(self.IMR, 0)
        for line in range(16):
            if not (imr >> line) & 1 or line in self._subscribed:
                continue
            pin = f"P{self._port_of(line)}{line}"
            net = self.mcu.net(pin)
            if net is None:
                continue
            self._subscribed[line] = pin
            self._level[line] = net.is_high
            net.listen(lambda n, line=line: self._edge(line, n))

    def _edge(self, line: int, net) -> None:
        was, now_high = self._level.get(line, False), net.is_high
        self._level[line] = now_high
        if not (self.regs.get(self.IMR, 0) >> line) & 1 or was == now_high:
            return
        rising = now_high and (self.regs.get(self.RTSR, 0) >> line) & 1
        falling = was and not now_high and (self.regs.get(self.FTSR, 0) >> line) & 1
        if rising or falling:
            self.pr |= 1 << line
            self.mcu.pend_irq(self.LINE_IRQ[line])

    def read(self, offset: int, size: int) -> int:
        if (offset & ~3) == self.PR:
            return self.pr
        return super().read(offset, size)

    def write(self, offset: int, value: int, size: int) -> None:
        off = offset & ~3
        if off == self.PR:
            self.pr &= ~value          # write-1-to-clear
        elif off == self.SWIER:
            for line in range(16):
                if (value >> line) & 1:
                    self.pr |= 1 << line
                    self.mcu.pend_irq(self.LINE_IRQ[line])
        else:
            super().write(offset, value, size)
            if off == self.IMR:
                self._resubscribe()
