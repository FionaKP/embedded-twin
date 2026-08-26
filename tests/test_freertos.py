"""Run the actual FreeRTOS kernel (ARM_CM3 port, unmodified) on the twin.

Requires examples/freertos-blinky/kernel/ (run fetch_kernel.sh once) and the
ziglang toolchain; skipped otherwise.
"""
from pathlib import Path

import pytest

from twin.build import build_twin
from twin.core.kernel import MS
from twin.cpu import cbuild
from twin.ir import BoardIR, ComponentIR, NetIR, NetNode

EX = Path(__file__).parent.parent / "examples" / "freertos-blinky"
KERNEL = EX / "kernel"

pytestmark = pytest.mark.skipif(
    not cbuild.zig_available() or not KERNEL.is_dir(),
    reason="needs ziglang + fetched FreeRTOS kernel (fetch_kernel.sh)")


def build_freertos_image() -> bytes:
    sources = [
        KERNEL / "tasks.c",
        KERNEL / "list.c",
        KERNEL / "queue.c",
        KERNEL / "portable" / "GCC" / "ARM_CM3" / "port.c",
        KERNEL / "portable" / "MemMang" / "heap_4.c",
        EX / "app" / "main.c",
        EX / "app" / "libc_shim.c",
    ]
    return cbuild.compile_project(
        sources, profile="stm32f4", with_crt0=True,
        include_dirs=[KERNEL / "include",
                      KERNEL / "portable" / "GCC" / "ARM_CM3",
                      EX / "app", EX / "app" / "shim_include"])


def freertos_board() -> BoardIR:
    b = BoardIR(name="freertos")
    b.add_component(ComponentIR(
        ref="U1", model="mcu.cortex_m",
        params={"profile": "stm32f4", "clock_hz": 16_000_000, "slice_us": 1000},
        pins={"1": "PA5", "2": "PA6", "3": "VDD"}))
    b.add_net(NetIR("LED5", "signal", [NetNode("U1", "1")]))
    b.add_net(NetIR("LED6", "signal", [NetNode("U1", "2")]))
    b.add_net(NetIR("+3V3", "power", [NetNode("U1", "3")]))
    return b


def test_freertos_two_tasks_blink_at_their_periods():
    image = build_freertos_image()
    twin = build_twin(freertos_board(), external_supplies={"+3V3": 3.3})
    twin.comp("U1").params["firmware"] = image
    twin.start()
    twin.kernel.run_until(2000 * MS)

    assert twin.comp("U1").state != "faulted", \
        [v for _t, _n, v in twin.kernel.trace.logs()]

    led5 = twin.kernel.trace.transitions("LED5")
    led6 = twin.kernel.trace.transitions("LED6")
    # 100 ms toggles -> ~20 edges in 2 s; 250 ms -> ~8
    assert 17 <= len(led5) <= 23, f"LED5 edges: {len(led5)}"
    assert 6 <= len(led6) <= 10, f"LED6 edges: {len(led6)}"

    d5 = [b - a for (a, _), (b, _) in zip(led5[1:], led5[2:])]
    d6 = [b - a for (a, _), (b, _) in zip(led6[1:], led6[2:])]
    assert all(90 * MS <= d <= 115 * MS for d in d5), d5[:5]
    assert all(240 * MS <= d <= 265 * MS for d in d6), d6[:5]
