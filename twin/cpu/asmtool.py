"""Assemble bare-metal Cortex-M firmware using the host clang.

Apple/LLVM clang assembles for --target=thumbv7m-none-eabi but macOS ships no
bare-metal linker, so this module is a micro-linker: it lays out .text (and
.rodata), then resolves the three relocation types tiny firmware needs:

  R_ARM_ABS32     (.word symbol — vector tables)
  R_ARM_THM_CALL  (bl symbol)
  R_ARM_THM_JUMP24 (b.w symbol)

Firmware images are flat binaries loaded at FLASH_BASE, vector table first.
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from elftools.elf.elffile import ELFFile

from .memmap import FLASH_BASE, INITIAL_SP

R_ARM_ABS32 = 2
R_ARM_THM_CALL = 10
R_ARM_THM_JUMP24 = 30

PRELUDE = """\
.syntax unified
.cpu cortex-m4
.thumb
"""


def clang_available() -> bool:
    return shutil.which("clang") is not None


def assemble(asm: str, base: int = FLASH_BASE) -> bytes:
    """Assemble one .s translation unit into a flat image placed at `base`."""
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "fw.s"
        obj = Path(td) / "fw.o"
        src.write_text(PRELUDE + asm)
        subprocess.run(
            ["clang", "--target=thumbv7m-none-eabi", "-c", str(src), "-o", str(obj)],
            check=True, capture_output=True, text=True)
        return _link_flat(obj, base)


def _link_flat(obj_path: Path, base: int) -> bytes:
    with open(obj_path, "rb") as f:
        elf = ELFFile(f)
        # lay out progbits sections: .text first, then others (.rodata...)
        placed: dict[str, int] = {}
        blob = bytearray()
        sections = sorted(
            (s for s in elf.iter_sections()
             if s.header.sh_type == "SHT_PROGBITS" and s.header.sh_flags & 0x2),
            key=lambda s: (s.name != ".text", s.name))
        for s in sections:
            while len(blob) % 4:
                blob.append(0)
            placed[s.name] = base + len(blob)
            blob += s.data()

        symtab = elf.get_section_by_name(".symtab")

        def sym_addr(idx: int) -> int:
            sym = symtab.get_symbol(idx)
            shndx = sym.entry.st_shndx
            if isinstance(shndx, int):
                sec_name = elf.get_section(shndx).name
                sec_base = placed.get(sec_name)
                if sec_base is None:
                    raise ValueError(f"symbol {sym.name!r} in unplaced section {sec_name}")
                return sec_base + sym.entry.st_value  # thumb bit already in st_value
            raise ValueError(f"unsupported symbol {sym.name!r} ({shndx})")

        for s in elf.iter_sections():
            if s.header.sh_type not in ("SHT_REL", "SHT_RELA"):
                continue
            target = elf.get_section(s.header.sh_info)
            if target.name not in placed:
                continue
            toff = placed[target.name] - base
            for rel in s.iter_relocations():
                off = toff + rel.entry.r_offset
                rtype = rel.entry.r_info_type
                addr_here = base + off
                if rtype == R_ARM_ABS32:
                    addend = struct.unpack_from("<I", blob, off)[0]
                    struct.pack_into("<I", blob, off, (sym_addr(rel.entry.r_info_sym) + addend) & 0xFFFFFFFF)
                elif rtype in (R_ARM_THM_CALL, R_ARM_THM_JUMP24):
                    dest = sym_addr(rel.entry.r_info_sym) & ~1
                    _patch_thm_branch(blob, off, dest - (addr_here + 4), rtype == R_ARM_THM_CALL)
                else:
                    raise ValueError(f"unsupported relocation type {rtype} in {target.name}")
        return bytes(blob)


def _patch_thm_branch(blob: bytearray, off: int, offset: int, is_call: bool) -> None:
    if not (-16777216 <= offset < 16777216):
        raise ValueError("branch out of range")
    s = (offset >> 24) & 1
    i1 = (offset >> 23) & 1
    i2 = (offset >> 22) & 1
    imm10 = (offset >> 12) & 0x3FF
    imm11 = (offset >> 1) & 0x7FF
    j1 = (~(i1 ^ s)) & 1
    j2 = (~(i2 ^ s)) & 1
    hi = 0xF000 | (s << 10) | imm10
    lo = (0xD000 if is_call else 0x9000) | 0x8000 | (j1 << 13) | (j2 << 11) | imm11
    struct.pack_into("<HH", blob, off, hi, lo)


def make_image(asm_body: str, entry_label: str = "reset") -> bytes:
    """Wrap an asm body with a 2-entry vector table (SP, reset) and assemble."""
    asm = f"""\
.section .text
.word {INITIAL_SP:#x}
.word {entry_label}
{asm_body}
"""
    return assemble(asm, FLASH_BASE)
