"""ARMv7-M system layer: SysTick, NVIC, SCB, and exception machinery.

Unicorn's bare M-class CPU implements the instruction set but not the
System Control Space devices (0xE000E000…) — that address range cannot even
be memory-mapped. So this module:

- emulates SysTick / NVIC / SCB register state in Python,
- services SCS loads/stores by catching the access fault, decoding the
  Thumb load/store, performing it against the emulated registers, and
  stepping the PC over the instruction,
- performs exception entry (manual ARMv7-M frame push) and exception
  return (catching the fault on the 0xFFFFFFFx magic PC and unstacking).

Exceptions are injected at slice boundaries, so interrupt latency is
bounded by the MCU's slice length (documented jitter, not a bug).
"""
from __future__ import annotations

import struct
from typing import TYPE_CHECKING, Callable, Optional

from ..core.kernel import SEC

SCS_BASE = 0xE000_0000
SCS_END = 0xE010_0000

# register offsets within 0xE000E000
_SYST_CSR = 0xE010
_SYST_RVR = 0xE014
_SYST_CVR = 0xE018
_SYST_CALIB = 0xE01C
_NVIC_ISER = 0xE100
_NVIC_ICER = 0xE180
_NVIC_ISPR = 0xE200
_NVIC_ICPR = 0xE280
_SCB_CPUID = 0xED00
_SCB_ICSR = 0xED04
_SCB_VTOR = 0xED08
_SCB_AIRCR = 0xED0C
_SCB_SHPR1 = 0xED18   # SHPR1..3: system handler priorities (exc 4..15)
_NVIC_IPR = 0xE400    # external IRQ priorities, one byte per IRQ

EXC_SVC = 11
EXC_PENDSV = 14
EXC_SYSTICK = 15
IRQ0_EXC = 16  # external IRQ n -> exception number 16+n


class SysTick:
    def __init__(self, clock_hz: int):
        self.clock_hz = clock_hz
        self.csr = 0
        self.rvr = 0
        self.next_fire_ns: Optional[int] = None
        self.countflag = False

    @property
    def enabled(self) -> bool:
        return bool(self.csr & 1) and self.rvr > 0

    @property
    def tickint(self) -> bool:
        return bool(self.csr & 2)

    def period_ns(self) -> int:
        return max(1, int((self.rvr + 1) * SEC / self.clock_hz))

    def write_csr(self, value: int, now: int) -> None:
        was = self.enabled
        self.csr = value & 0x7
        if self.enabled and not was:
            self.next_fire_ns = now + self.period_ns()
        elif not self.enabled:
            self.next_fire_ns = None

    def advance(self, now: int) -> bool:
        """Returns True if the timer expired since last check (catch-up)."""
        if not self.enabled or self.next_fire_ns is None or now < self.next_fire_ns:
            return False
        period = self.period_ns()
        while self.next_fire_ns <= now:
            self.next_fire_ns += period
        self.countflag = True
        return True

    def read_cvr(self, now: int) -> int:
        if not self.enabled or self.next_fire_ns is None:
            return 0
        remaining_ns = max(0, self.next_fire_ns - now)
        return int(remaining_ns * self.clock_hz / SEC) % (self.rvr + 1)


class Armv7mSystem:
    """SCS register file + pending/active exception state."""

    def __init__(self, clock_hz: int, log: Callable[[str], None] = lambda m: None):
        self.systick = SysTick(clock_hz)
        self.nvic_enabled = 0          # bitmask over 96 IRQs (banked regs)
        self.pending: set[int] = set() # exception numbers (15 = systick, 16+n = IRQ n)
        self.active: list[tuple[int, int]] = []  # nested (exc, priority) stack
        self.vtor = 0
        self.regs: dict[int, int] = {} # storage for everything else
        self.log = log
        self.now = 0
        self.reset_requested = False

    # -- priorities (8-bit, lower value = higher priority) -----------------
    def priority_of(self, exc: int) -> int:
        if exc >= IRQ0_EXC:
            off = _NVIC_IPR + (exc - IRQ0_EXC)
        elif 4 <= exc <= 15:
            off = _SCB_SHPR1 + (exc - 4)
        else:
            return 0
        word = self.regs.get(off & ~3, 0)
        return (word >> ((off & 3) * 8)) & 0xFF

    def active_priority(self) -> int:
        return min((pri for _exc, pri in self.active), default=256)

    def _injectable(self, exc: int, basepri: int, primask: int) -> bool:
        if exc >= IRQ0_EXC and not (self.nvic_enabled >> (exc - IRQ0_EXC)) & 1:
            return False
        if primask & 1:
            return False
        pri = self.priority_of(exc)
        if basepri and pri >= basepri:
            return False
        return pri < self.active_priority()

    def take_pending(self, basepri: int = 0, primask: int = 0) -> Optional[int]:
        """Best pending exception allowed to run now (preemption-aware)."""
        best = None
        for exc in self.pending:
            if not self._injectable(exc, basepri, primask):
                continue
            key = (self.priority_of(exc), exc)
            if best is None or key < best:
                best = key
        if best is None:
            return None
        self.pending.discard(best[1])
        return best[1]

    def has_injectable(self, basepri: int = 0, primask: int = 0) -> bool:
        return any(self._injectable(e, basepri, primask) for e in self.pending)

    # -- time -------------------------------------------------------------
    def advance(self, now: int) -> None:
        self.now = now
        if self.systick.advance(now) and self.systick.tickint:
            self.pending.add(EXC_SYSTICK)

    def next_wake_ns(self) -> Optional[int]:
        """When should a WFI sleep end? None = no wake source armed."""
        if self.systick.enabled and self.systick.tickint:
            return self.systick.next_fire_ns
        return None

    def pend_irq(self, irq: int) -> bool:
        """Pend external IRQ n; returns True if it is enabled (will fire).
        A disabled IRQ stays pending (ISPR) and fires when later enabled."""
        self.pending.add(IRQ0_EXC + irq)
        return bool((self.nvic_enabled >> irq) & 1)

    # -- register file ----------------------------------------------------
    def read(self, addr: int, size: int) -> int:
        if size < 4:  # sub-word access: extract from the aligned word
            word = self.read(addr & ~3, 4)
            return (word >> ((addr & 3) * 8)) & ((1 << (8 * size)) - 1)
        off = addr - 0xE000_0000
        if off == _SYST_CSR:
            v = self.systick.csr | 0x4  # CLKSOURCE reads as core clock
            if self.systick.countflag:
                v |= 1 << 16
                self.systick.countflag = False
            return v
        if off == _SYST_RVR:
            return self.systick.rvr
        if off == _SYST_CVR:
            return self.systick.read_cvr(self.now)
        if off == _SYST_CALIB:
            return int(self.systick.clock_hz / 100)  # 10 ms calibration value
        for bank_base in (_NVIC_ISER, _NVIC_ICER):
            if bank_base <= off < bank_base + 12:       # ISERx/ICERx, 96 IRQs
                bank = (off - bank_base) // 4
                return (self.nvic_enabled >> (32 * bank)) & 0xFFFFFFFF
        for bank_base in (_NVIC_ISPR, _NVIC_ICPR):
            if bank_base <= off < bank_base + 12:
                bank = (off - bank_base) // 4
                bits = 0
                for exc in self.pending:
                    irq = exc - IRQ0_EXC
                    if irq >= 0 and irq // 32 == bank:
                        bits |= 1 << (irq % 32)
                return bits
        if off == _SCB_CPUID:
            return 0x410FC241  # Cortex-M4 r0p1
        if off == _SCB_VTOR:
            return self.vtor
        if off == _SCB_AIRCR:
            return 0xFA05_0000
        if off == _SCB_ICSR:
            v = self.active[-1][0] if self.active else 0
            if EXC_SYSTICK in self.pending:
                v |= 1 << 26
            if EXC_PENDSV in self.pending:
                v |= 1 << 28
            return v
        return self.regs.get(off, 0)

    def write(self, addr: int, value: int, size: int) -> None:
        if size < 4:  # sub-word store: merge into the aligned word
            shift = (addr & 3) * 8
            mask = ((1 << (8 * size)) - 1) << shift
            cur = self.read(addr & ~3, 4)
            self.write(addr & ~3, (cur & ~mask) | ((value << shift) & mask), 4)
            return
        off = addr - 0xE000_0000
        if off == _SYST_CSR:
            self.systick.write_csr(value, self.now)
        elif off == _SYST_RVR:
            self.systick.rvr = value & 0xFFFFFF
        elif off == _SYST_CVR:
            self.systick.countflag = False  # any write clears
        elif _NVIC_ISER <= off < _NVIC_ISER + 12:
            self.nvic_enabled |= value << (32 * ((off - _NVIC_ISER) // 4))
        elif _NVIC_ICER <= off < _NVIC_ICER + 12:
            self.nvic_enabled &= ~(value << (32 * ((off - _NVIC_ICER) // 4)))
        elif _NVIC_ISPR <= off < _NVIC_ISPR + 12:
            base_irq = 32 * ((off - _NVIC_ISPR) // 4)
            for i in range(32):
                if (value >> i) & 1:
                    self.pending.add(IRQ0_EXC + base_irq + i)
        elif _NVIC_ICPR <= off < _NVIC_ICPR + 12:
            base_irq = 32 * ((off - _NVIC_ICPR) // 4)
            for i in range(32):
                if (value >> i) & 1:
                    self.pending.discard(IRQ0_EXC + base_irq + i)
        elif off == _SCB_VTOR:
            self.vtor = value & 0xFFFFFF80
        elif off == _SCB_ICSR:
            if value & (1 << 26):   # PENDSTSET
                self.pending.add(EXC_SYSTICK)
            if value & (1 << 25):   # PENDSTCLR
                self.pending.discard(EXC_SYSTICK)
            if value & (1 << 28):   # PENDSVSET
                self.pending.add(EXC_PENDSV)
            if value & (1 << 27):   # PENDSVCLR
                self.pending.discard(EXC_PENDSV)
        elif off == _SCB_AIRCR:
            if value & (1 << 2):    # SYSRESETREQ
                self.reset_requested = True
                self.log("SYSRESETREQ via AIRCR")
        else:
            self.regs[off] = value


# -- Thumb load/store mini-decoder (for SCS access fixup) ------------------

class DecodedMemOp:
    __slots__ = ("is_load", "rt", "rn", "rm", "rm_shift", "imm", "size",
                 "width", "post", "wback")

    def __init__(self, is_load, rt, rn, imm, size, width, rm=None,
                 rm_shift=0, post=False, wback=False):
        self.is_load, self.rt, self.rn, self.rm = is_load, rt, rn, rm
        self.rm_shift = rm_shift
        self.imm, self.size, self.width = imm, size, width
        self.post, self.wback = post, wback

    def offset(self, rm_value: int = 0) -> int:
        return (rm_value << self.rm_shift) if self.rm is not None else self.imm


def decode_mem_op(code: bytes) -> Optional[DecodedMemOp]:
    """Decode the Thumb load/store at `code`. Returns None if unsupported."""
    hw1 = struct.unpack_from("<H", code)[0]
    top = hw1 & 0xF800
    if top in (0x6000, 0x6800):    # STR/LDR rt, [rn, #imm5*4]
        return DecodedMemOp(top == 0x6800, hw1 & 7, (hw1 >> 3) & 7,
                            ((hw1 >> 6) & 0x1F) * 4, 4, 2)
    if top in (0x7000, 0x7800):    # STRB/LDRB
        return DecodedMemOp(top == 0x7800, hw1 & 7, (hw1 >> 3) & 7,
                            (hw1 >> 6) & 0x1F, 1, 2)
    if top in (0x8000, 0x8800):    # STRH/LDRH
        return DecodedMemOp(top == 0x8800, hw1 & 7, (hw1 >> 3) & 7,
                            ((hw1 >> 6) & 0x1F) * 2, 2, 2)
    if (hw1 & 0xFE00) in (0x5000, 0x5800):  # STR/LDR rt, [rn, rm]
        return DecodedMemOp((hw1 & 0xFE00) == 0x5800, hw1 & 7, (hw1 >> 3) & 7,
                            0, 4, 2, rm=(hw1 >> 6) & 7)
    if len(code) >= 4 and (hw1 & 0xFE00) == 0xF800:
        # Thumb-2 single load/store: 1111 100 F size L rn
        hw2 = struct.unpack_from("<H", code, 2)[0]
        size = {0: 1, 1: 2, 2: 4}.get((hw1 >> 5) & 3)
        if size is None:
            return None
        is_load = bool(hw1 & 0x10)
        rn, rt = hw1 & 0xF, (hw2 >> 12) & 0xF
        if hw1 & 0x80:                      # T3: [rn, #imm12]
            return DecodedMemOp(is_load, rt, rn, hw2 & 0xFFF, size, 4)
        if hw2 & 0x0800:                    # T4: [rn, #±imm8], pre/post, wback
            imm = hw2 & 0xFF
            if not (hw2 & 0x0200):          # U=0: negative offset
                imm = -imm
            pre = bool(hw2 & 0x0400)
            wback = bool(hw2 & 0x0100)
            return DecodedMemOp(is_load, rt, rn, imm, size, 4,
                                post=not pre, wback=wback)
        if (hw2 & 0x0FC0) == 0:             # register: [rn, rm, lsl #n]
            return DecodedMemOp(is_load, rt, rn, 0, size, 4,
                                rm=hw2 & 0xF, rm_shift=(hw2 >> 4) & 3)
    return None
