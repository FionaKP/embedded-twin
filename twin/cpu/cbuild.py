"""Compile real C firmware with the pip-installable zig toolchain.

`ziglang` ships clang+lld, so bare-metal Cortex-M C firmware builds on any
host with zero system toolchain — the same low-barrier philosophy as the
rest of the project. Used by tests and examples; optional at runtime.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

_LINKER_SCRIPTS = {
    # profile -> (flash_origin, flash_len, ram_origin, ram_len)
    "stm32f4": (0x0800_0000, "1024K", 0x2000_0000, "128K"),
    "twin": (0x0800_0000, "256K", 0x2000_0000, "64K"),
}


def zig_available() -> bool:
    return importlib.util.find_spec("ziglang") is not None


def linker_script(profile: str) -> str:
    flash_org, flash_len, ram_org, ram_len = _LINKER_SCRIPTS[profile]
    return f"""\
MEMORY {{ FLASH (rx) : ORIGIN = {flash_org:#x}, LENGTH = {flash_len}
          RAM (rwx)  : ORIGIN = {ram_org:#x}, LENGTH = {ram_len} }}
_estack = ORIGIN(RAM) + LENGTH(RAM);
SECTIONS {{
  .text : {{ KEEP(*(.vectors)) *(.text*) *(.rodata*) }} > FLASH
  .data : {{ *(.data*) }} > RAM AT > FLASH
  .bss  : {{ *(.bss*) }} > RAM
}}
"""


def compile_c(code: str, profile: str = "stm32f4",
              extra_flags: list[str] | None = None) -> bytes:
    """C source -> flat firmware image (vector table first). Requires the
    source to place its vector table in a `.vectors` section.

    Note: .data initializers are not copied by a startup file here — keep
    globals zero-initialized (.bss) or const (.rodata) unless the firmware
    brings its own startup code.
    """
    if not zig_available():
        raise RuntimeError("ziglang not installed (pip install ziglang)")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "fw.c").write_text(code)
        (td / "link.ld").write_text(linker_script(profile))
        zig = [sys.executable, "-m", "ziglang"]
        subprocess.run(
            zig + ["cc", "-target", "thumb-freestanding-eabi",
                   "-mcpu=cortex_m4", "-O2", "-nostdlib", "-ffreestanding",
                   "-fno-builtin", "-Wall",
                   *(extra_flags or []),
                   "-T", str(td / "link.ld"),
                   str(td / "fw.c"), "-o", str(td / "fw.elf")],
            check=True, capture_output=True, text=True)
        subprocess.run(
            zig + ["objcopy", "-O", "binary", str(td / "fw.elf"),
                   str(td / "fw.bin")],
            check=True, capture_output=True, text=True)
        return (td / "fw.bin").read_bytes()
