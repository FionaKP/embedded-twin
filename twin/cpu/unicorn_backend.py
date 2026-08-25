"""Unicorn-based Cortex-M firmware execution (ADR-0003).

Execution advances in time slices synchronized to kernel time: each slice
runs `clock_hz * slice_ns` instructions, so firmware time and board time
stay locked (cycle-approximate: 1 instruction ≈ 1 cycle). Loads/stores in
the peripheral window dispatch to the owning MCU component's callbacks.

Firmware that writes CTRL_SLEEP_US fast-forwards: emulation stops and
resumes after the sleep, and the MCU drops to its sleep current — this is
how days of battery life simulate in seconds.
"""
from __future__ import annotations

import struct
from typing import TYPE_CHECKING, Callable, Optional

from unicorn import (Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS,
                     UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UcError)
from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_SP

from elftools.elf.elffile import ELFFile

from ..core.kernel import US
from . import memmap as mm

if TYPE_CHECKING:
    pass


class UnicornMCU:
    def __init__(self, clock_hz: int,
                 periph_read: Callable[[int], int],
                 periph_write: Callable[[int, int], None],
                 slice_ns: int = 1000 * US):
        self.clock_hz = clock_hz
        self.slice_ns = slice_ns
        self.periph_read = periph_read
        self.periph_write = periph_write
        self.halted = False
        self.sleep_request_us: Optional[int] = None

        uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        uc.mem_map(mm.FLASH_BASE, mm.FLASH_SIZE)
        uc.mem_map(0x0000_0000, mm.FLASH_SIZE)  # boot alias of flash
        uc.mem_map(mm.RAM_BASE, mm.RAM_SIZE)
        uc.mem_map(mm.PERIPH_BASE, mm.PERIPH_SIZE)
        uc.hook_add(UC_HOOK_MEM_READ, self._on_read,
                    begin=mm.PERIPH_BASE, end=mm.PERIPH_BASE + mm.PERIPH_SIZE - 1)
        uc.hook_add(UC_HOOK_MEM_WRITE, self._on_write,
                    begin=mm.PERIPH_BASE, end=mm.PERIPH_BASE + mm.PERIPH_SIZE - 1)
        self.uc = uc
        self.pc = 0
        # set by the owner when a CTRL write stopped emulation: unicorn leaves
        # PC on the store instruction, so we must step over it on resume
        self.skip_current = False

    # -- image loading ----------------------------------------------------
    def load_bin(self, image: bytes, base: int = mm.FLASH_BASE) -> None:
        self.uc.mem_write(base, image)
        self.uc.mem_write(0x0, image[:min(len(image), mm.FLASH_SIZE)])
        sp, reset = struct.unpack_from("<II", image, 0)
        self.uc.reg_write(UC_ARM_REG_SP, sp)
        self.pc = reset | 1

    def load_elf(self, path: str) -> None:
        with open(path, "rb") as f:
            elf = ELFFile(f)
            entry = elf.header.e_entry
            for seg in elf.iter_segments():
                if seg.header.p_type == "PT_LOAD" and seg.header.p_filesz:
                    self.uc.mem_write(seg.header.p_paddr, seg.data())
        vec = self.uc.mem_read(mm.FLASH_BASE, 8)
        sp, reset = struct.unpack("<II", bytes(vec))
        self.uc.reg_write(UC_ARM_REG_SP, sp)
        self.pc = (reset | 1) if reset else (entry | 1)

    # -- peripheral hooks -------------------------------------------------
    def _on_read(self, uc, access, address, size, value, data):
        v = self.periph_read(address) & 0xFFFFFFFF
        uc.mem_write(address & ~3, struct.pack("<I", v))
        return True

    def _on_write(self, uc, access, address, size, value, data):
        self.periph_write(address, value & 0xFFFFFFFF)
        return True

    # -- execution --------------------------------------------------------
    def run_slice(self) -> None:
        """Execute one slice worth of instructions (unless halted/sleeping)."""
        if self.halted:
            return
        count = max(1, int(self.clock_hz * self.slice_ns / 1_000_000_000))
        try:
            self.uc.emu_start(self.pc, 0xFFFFFFFE, count=count)
        except UcError as e:
            self.halted = True
            raise RuntimeError(f"firmware fault at pc={self.uc.reg_read(UC_ARM_REG_PC):#010x}: {e}") from e
        pc = self.uc.reg_read(UC_ARM_REG_PC)
        if self.skip_current:
            self.skip_current = False
            pc += self._thumb_isize(pc)
        self.pc = pc | 1

    def _thumb_isize(self, pc: int) -> int:
        hw = struct.unpack("<H", bytes(self.uc.mem_read(pc & ~1, 2)))[0]
        return 4 if (hw & 0xF800) in (0xE800, 0xF000, 0xF800) else 2

    def stop(self) -> None:
        self.uc.emu_stop()
