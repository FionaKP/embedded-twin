"""Unicorn-based Cortex-M firmware execution (ADR-0003).

Execution advances in time slices synchronized to kernel time: each slice
runs `clock_hz * slice_ns` instructions (cycle-approximate: 1 instruction
≈ 1 cycle). Loads/stores in the peripheral window dispatch to Python
peripheral models.

Two operating shapes:
- legacy TwinMCU map (v0.1): flat peripheral callbacks, CTRL_SLEEP_US
  fast-forward register.
- vendor profiles (v0.2): a PeripheralBus plus an ARMv7-M system layer
  (SysTick/NVIC/SCB) with manual exception entry/return and WFI-as-sleep,
  so register-level STM32 firmware runs unmodified.

Unicorn quirks handled here (each empirically probed):
- emu_stop() from a mem hook leaves PC on the trapping instruction: decode
  the Thumb width and step over it on resume.
- The System Control Space (0xE000E000…) cannot be memory-mapped in MCLASS
  mode: SCS accesses fault, and we service them by decoding the load/store
  and executing it against the emulated system registers.
- EXC_RETURN magic addresses raise UC_ERR_EXCEPTION: we unstack manually.
- WFI halts emulation with PC already past the instruction.
"""
from __future__ import annotations

import struct
from typing import Callable, Optional

from unicorn import (Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS,
                     UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UcError)
from unicorn import arm_const as A

from elftools.elf.elffile import ELFFile

from ..core.kernel import US
from . import memmap as mm
from .armv7m import SCS_BASE, SCS_END, Armv7mSystem, decode_mem_op

_GPR = [A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3,
        A.UC_ARM_REG_R4, A.UC_ARM_REG_R5, A.UC_ARM_REG_R6, A.UC_ARM_REG_R7,
        A.UC_ARM_REG_R8, A.UC_ARM_REG_R9, A.UC_ARM_REG_R10, A.UC_ARM_REG_R11,
        A.UC_ARM_REG_R12, A.UC_ARM_REG_SP, A.UC_ARM_REG_LR, A.UC_ARM_REG_PC]

_PRIMASK_REG = getattr(A, "UC_ARM_REG_PRIMASK", None)

_WFI_OPCODES = (0xBF30, 0xBF20)  # wfi, wfe


class UnicornMCU:
    def __init__(self, clock_hz: int,
                 periph_read: Callable[[int, int], int],
                 periph_write: Callable[[int, int, int], None],
                 slice_ns: int = 1000 * US,
                 flash: tuple[int, int] = (mm.FLASH_BASE, mm.FLASH_SIZE),
                 ram_regions: Optional[list[tuple[int, int]]] = None,
                 periph_window: tuple[int, int] = (mm.PERIPH_BASE, mm.PERIPH_SIZE),
                 system: Optional[Armv7mSystem] = None,
                 log: Callable[[str], None] = lambda m: None):
        self.clock_hz = clock_hz
        self.slice_ns = slice_ns
        self.periph_read = periph_read
        self.periph_write = periph_write
        self.system = system
        self.log = log
        self.halted = False
        self.skip_current = False   # legacy CTRL-write stop: step over store
        self.flash = flash
        self.pc = 0

        uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        uc.mem_map(flash[0], flash[1])
        if flash[0] != 0:
            uc.mem_map(0x0000_0000, flash[1])   # boot alias of flash
        for base, size in (ram_regions or [(mm.RAM_BASE, mm.RAM_SIZE)]):
            uc.mem_map(base, size)
        pw_base, pw_size = periph_window
        uc.mem_map(pw_base, pw_size)
        uc.hook_add(UC_HOOK_MEM_READ, self._on_read,
                    begin=pw_base, end=pw_base + pw_size - 1)
        uc.hook_add(UC_HOOK_MEM_WRITE, self._on_write,
                    begin=pw_base, end=pw_base + pw_size - 1)
        self.uc = uc

    # -- image loading ----------------------------------------------------
    def load_bin(self, image: bytes, base: Optional[int] = None) -> None:
        base = self.flash[0] if base is None else base
        self.uc.mem_write(base, image)
        if base != 0:
            self.uc.mem_write(0x0, image[:min(len(image), self.flash[1])])
        self._reset_from_vector(bytes(image[:8]))

    def load_elf(self, path: str) -> None:
        with open(path, "rb") as f:
            elf = ELFFile(f)
            for seg in elf.iter_segments():
                if seg.header.p_type == "PT_LOAD" and seg.header.p_filesz:
                    self.uc.mem_write(seg.header.p_paddr, seg.data())
                    if seg.header.p_paddr == self.flash[0]:
                        self.uc.mem_write(0x0, seg.data()[:self.flash[1]])
        vec = bytes(self.uc.mem_read(self.flash[0], 8))
        self._reset_from_vector(vec)

    def _reset_from_vector(self, vec8: bytes) -> None:
        sp, reset = struct.unpack("<II", vec8)
        self.uc.reg_write(A.UC_ARM_REG_SP, sp)
        self.pc = reset | 1
        if self.system:
            self.system.active.clear()
            self.system.pending.clear()

    # -- peripheral hooks -------------------------------------------------
    def _on_read(self, uc, access, address, size, value, data):
        v = self.periph_read(address, size) & 0xFFFFFFFF
        uc.mem_write(address & ~3, struct.pack("<I", v))
        return True

    def _on_write(self, uc, access, address, size, value, data):
        self.periph_write(address, value & 0xFFFFFFFF, size)
        return True

    # -- execution --------------------------------------------------------
    def run_slice(self, slice_ns: Optional[int] = None) -> str:
        """Execute one slice. Returns 'ran' | 'wfi' | 'halted' | 'reset'.
        Raises RuntimeError on a genuine firmware fault."""
        if self.halted:
            return "halted"
        if self.system is not None:
            if self.system.reset_requested:
                self.system.reset_requested = False
                vec = bytes(self.uc.mem_read(self.flash[0], 8))
                self._reset_from_vector(vec)
                return "reset"
        ns = self.slice_ns if slice_ns is None else slice_ns
        count = max(1, int(self.clock_hz * ns / 1_000_000_000))
        budget = 64  # SCS fixups / SVCs / returns can interrupt a slice often
        while budget:
            budget -= 1
            self._maybe_inject()  # also tail-chains after exception returns
            try:
                self.uc.emu_start(self.pc, 0xFFFFFFFE, count=count)
            except UcError as e:
                if self._handle_fault(e):
                    continue     # serviced (SCS access / SVC / exception return)
                raise RuntimeError(
                    f"firmware fault at pc={self.uc.reg_read(A.UC_ARM_REG_PC):#010x}: {e}"
                ) from None
            break

        pc = self.uc.reg_read(A.UC_ARM_REG_PC)
        if self.skip_current:
            self.skip_current = False
            pc += self._thumb_isize(pc)
        self.pc = pc | 1
        if self.halted:
            return "halted"
        if self.system is not None and self._just_ran_wfi(pc):
            return "wfi"
        return "ran"

    def _just_ran_wfi(self, pc: int) -> bool:
        try:
            prev = struct.unpack("<H", bytes(self.uc.mem_read((pc & ~1) - 2, 2)))[0]
        except UcError:
            return False
        return prev in _WFI_OPCODES

    # -- fault servicing ---------------------------------------------------
    def _handle_fault(self, err: UcError) -> bool:
        pc = self.uc.reg_read(A.UC_ARM_REG_PC)
        if (pc & 0xFFFFFF00) == 0xFFFFFF00:
            return self._exception_return(pc)
        if self._was_svc(pc):
            return self._svc_entry(pc)
        return self._scs_fixup(pc)

    def _was_svc(self, pc: int) -> bool:
        """PC sits just after an `svc #imm` (Unicorn advances PC before
        raising UC_ERR_EXCEPTION for it)."""
        try:
            hw = struct.unpack("<H", bytes(self.uc.mem_read((pc & ~1) - 2, 2)))[0]
        except UcError:
            return False
        return (hw & 0xFF00) == 0xDF00

    def _svc_entry(self, pc: int) -> bool:
        """Synchronous SVCall: taken immediately (exception 11)."""
        if self.system is None:
            return False
        self.pc = pc | 1     # return address = after the svc
        self._enter_exception(11)
        return True

    def _scs_fixup(self, pc: int) -> bool:
        if self.system is None:
            return False
        pc &= ~1
        try:
            code = bytes(self.uc.mem_read(pc, 4))
        except UcError:
            return False
        op = decode_mem_op(code)
        if op is None:
            return False
        base = self.uc.reg_read(_GPR[op.rn])
        offset = op.offset(self.uc.reg_read(_GPR[op.rm]) if op.rm is not None else 0)
        addr = (base if op.post else base + offset) & 0xFFFFFFFF
        if not (SCS_BASE <= addr < SCS_END):
            return False
        if op.is_load:
            val = self.system.read(addr, op.size)
            self.uc.reg_write(_GPR[op.rt], val & ((1 << (8 * op.size)) - 1))
        else:
            val = self.uc.reg_read(_GPR[op.rt]) & ((1 << (8 * op.size)) - 1)
            self.system.write(addr, val, op.size)
        if op.post or op.wback:
            self.uc.reg_write(_GPR[op.rn], (base + offset) & 0xFFFFFFFF)
        self.pc = (pc + op.width) | 1
        return True

    # -- exception machinery (ARMv7-M B1.5, banked MSP/PSP) ----------------
    def _mask_state(self) -> tuple[int, int]:
        primask = self.uc.reg_read(_PRIMASK_REG) if _PRIMASK_REG is not None else 0
        basepri = (self.uc.reg_read(A.UC_ARM_REG_BASEPRI)
                   if hasattr(A, "UC_ARM_REG_BASEPRI") else 0)
        return basepri & 0xFF, primask & 1

    def can_wake(self) -> bool:
        """Is there a pending exception that would run right now? (WFI exit)"""
        if self.system is None:
            return False
        basepri, primask = self._mask_state()
        return self.system.has_injectable(basepri, primask)

    def _maybe_inject(self) -> None:
        if self.system is None:
            return
        basepri, primask = self._mask_state()
        exc = self.system.take_pending(basepri, primask)
        if exc is not None:
            self._enter_exception(exc)

    def _enter_exception(self, exc: int) -> None:
        sys_ = self.system
        control = self.uc.reg_read(A.UC_ARM_REG_CONTROL)
        in_handler = bool(sys_.active)
        on_psp = (not in_handler) and bool(control & 2)

        sp = self.uc.reg_read(A.UC_ARM_REG_PSP if on_psp else A.UC_ARM_REG_MSP)
        xpsr = self.uc.reg_read(A.UC_ARM_REG_XPSR)
        align = 0
        if sp & 7:
            sp -= 4
            align = 1
        sp -= 32
        frame = struct.pack(
            "<8I",
            self.uc.reg_read(A.UC_ARM_REG_R0), self.uc.reg_read(A.UC_ARM_REG_R1),
            self.uc.reg_read(A.UC_ARM_REG_R2), self.uc.reg_read(A.UC_ARM_REG_R3),
            self.uc.reg_read(A.UC_ARM_REG_R12), self.uc.reg_read(A.UC_ARM_REG_LR),
            self.pc & ~1, (xpsr | (align << 9)) & 0xFFFFFFFF)
        self.uc.mem_write(sp, frame)
        self.uc.reg_write(A.UC_ARM_REG_PSP if on_psp else A.UC_ARM_REG_MSP, sp)

        if in_handler:
            exc_return = 0xFFFFFFF1
        elif on_psp:
            exc_return = 0xFFFFFFFD
            # handlers always run on MSP: clear SPSEL (re-banks SP)
            self.uc.reg_write(A.UC_ARM_REG_CONTROL, control & ~2)
        else:
            exc_return = 0xFFFFFFF9
        self.uc.reg_write(A.UC_ARM_REG_LR, exc_return)
        self.uc.reg_write(A.UC_ARM_REG_XPSR, 0x01000000 | exc)
        vec = sys_.vtor + exc * 4
        handler = struct.unpack("<I", bytes(self.uc.mem_read(vec, 4)))[0]
        self.pc = handler | 1
        sys_.active.append((exc, sys_.priority_of(exc)))

    def _exception_return(self, exc_return: int) -> bool:
        if self.system is None or not self.system.active:
            return False
        # NB: unicorn strips bit0 of the magic PC (0xFFFFFFFD reads as ...FC),
        # so decode the architectural bits: bit2 = return stack (1 = PSP),
        # bit3 = return mode (1 = thread)
        to_psp = bool(exc_return & 0x4)
        to_thread = bool(exc_return & 0x8)
        sp_reg = A.UC_ARM_REG_PSP if to_psp else A.UC_ARM_REG_MSP
        sp = self.uc.reg_read(sp_reg)
        r0, r1, r2, r3, r12, lr, ret, xpsr = struct.unpack(
            "<8I", bytes(self.uc.mem_read(sp, 32)))
        sp += 32
        if xpsr & (1 << 9):
            sp += 4
        for reg, val in ((A.UC_ARM_REG_R0, r0), (A.UC_ARM_REG_R1, r1),
                         (A.UC_ARM_REG_R2, r2), (A.UC_ARM_REG_R3, r3),
                         (A.UC_ARM_REG_R12, r12), (A.UC_ARM_REG_LR, lr)):
            self.uc.reg_write(reg, val)
        self.uc.reg_write(sp_reg, sp)
        control = self.uc.reg_read(A.UC_ARM_REG_CONTROL)
        self.system.active.pop()
        # thread-mode return selects the stack; nested return stays on MSP
        if to_thread:
            new_control = (control | 2) if to_psp else (control & ~2)
            self.uc.reg_write(A.UC_ARM_REG_CONTROL, new_control)
        self.uc.reg_write(A.UC_ARM_REG_XPSR, xpsr & ~(1 << 9))
        self.pc = ret | 1
        return True

    # -- misc --------------------------------------------------------------
    def _thumb_isize(self, pc: int) -> int:
        hw = struct.unpack("<H", bytes(self.uc.mem_read(pc & ~1, 2)))[0]
        return 4 if (hw & 0xF800) in (0xE800, 0xF000, 0xF800) else 2

    def stop(self) -> None:
        self.uc.emu_stop()
