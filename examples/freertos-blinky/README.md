# Example: FreeRTOS on the twin

The **unmodified FreeRTOS kernel** (V11.1.0, ARM_CM3 port) running on the
STM32F4 profile: SVC starts the scheduler, SysTick drives the tick, PendSV
context-switches tasks on their PSP stacks, BASEPRI guards critical
sections — all of it on the emulated core, with two tasks blinking PA5
(100 ms) and PA6 (250 ms) via `vTaskDelay`.

```bash
./fetch_kernel.sh        # downloads the pinned kernel release (gitignored)
pytest tests/test_freertos.py -v
```

[app/](app/) holds `main.c`, `FreeRTOSConfig.h`, and freestanding libc
shims; the kernel compiles with the zig toolchain (`pip install ziglang`) —
no system cross-compiler, no vendor IDE.

Known simplification: interrupts inject at slice boundaries (≤ `slice_us`
jitter) and instruction counts are cycle-approximate. Tick-driven schedulers
are unaffected; cycle-exact profiling is out of scope (ADR-0003).
