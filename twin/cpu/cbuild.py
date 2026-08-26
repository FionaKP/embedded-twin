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
    "nrf52": (0x0000_0000, "512K", 0x2000_0000, "64K"),
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
  .data : {{ _sdata = .; *(.data*) _edata = .; }} > RAM AT > FLASH
  _sidata = LOADADDR(.data);
  .bss  : {{ _sbss = .; *(.bss*) *(COMMON) _ebss = .; }} > RAM
}}
"""


# Optional startup: copies .data from flash and zeroes .bss, then calls main.
# Firmware that defines its own Reset_Handler doesn't need it; firmware with
# initialized globals (like FreeRTOS) does.
CRT0 = r"""
#include <stdint.h>
extern uint32_t _sidata, _sdata, _edata, _sbss, _ebss;
extern int main(void);
void Reset_Handler(void) {
    uint32_t *src = &_sidata, *dst = &_sdata;
    while (dst < &_edata) *dst++ = *src++;
    for (dst = &_sbss; dst < &_ebss;) *dst++ = 0;
    main();
    for (;;) {}
}
"""


def compile_c(code: str, profile: str = "stm32f4",
              extra_flags: list[str] | None = None) -> bytes:
    """C source -> flat firmware image (vector table first). Requires the
    source to place its vector table in a `.vectors` section.

    Note: .data initializers are not copied by a startup file here — keep
    globals zero-initialized (.bss) or const (.rodata) unless the firmware
    brings its own startup code.
    """
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "fw.c"
        src.write_text(code)
        return compile_project([src], profile=profile, extra_flags=extra_flags,
                               work_dir=Path(td))


def compile_project(sources: list[Path], profile: str = "stm32f4",
                    include_dirs: list[Path] | None = None,
                    extra_flags: list[str] | None = None,
                    with_crt0: bool = False,
                    work_dir: Path | None = None) -> bytes:
    """Compile+link a multi-file bare-metal project into a flat image."""
    if not zig_available():
        raise RuntimeError("ziglang not installed (pip install ziglang)")
    import contextlib
    ctx = contextlib.nullcontext(work_dir) if work_dir else tempfile.TemporaryDirectory()
    with ctx as td:
        td = Path(td)
        (td / "link.ld").write_text(linker_script(profile))
        srcs = [str(s) for s in sources]
        if with_crt0:
            (td / "crt0.c").write_text(CRT0)
            srcs.append(str(td / "crt0.c"))
        zig = [sys.executable, "-m", "ziglang"]
        try:
            subprocess.run(
                zig + ["cc", "-target", "thumb-freestanding-eabi",
                       "-mcpu=cortex_m4", "-O2", "-nostdlib", "-ffreestanding",
                       "-fno-builtin", "-Wall",
                       *[f"-I{d}" for d in (include_dirs or [])],
                       *(extra_flags or []),
                       "-T", str(td / "link.ld"),
                       *srcs, "-o", str(td / "fw.elf")],
                check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"firmware build failed:\n{e.stderr[-3000:]}") from None
        subprocess.run(
            zig + ["objcopy", "-O", "binary", str(td / "fw.elf"),
                   str(td / "fw.bin")],
            check=True, capture_output=True, text=True)
        return (td / "fw.bin").read_bytes()
