# Example: STM32 button + LED, real C firmware

The v0.2 headline: **register-level STM32F4 C firmware runs unmodified.**
[firmware/main.c](firmware/main.c) is ordinary bare-metal code — RCC clock
enable, GPIO MODER/ODR/IDR, SysTick interrupt at real addresses, WFI sleep —
compiled on the fly by the scenario runner (zig toolchain, `pip install
ziglang`, no system toolchain needed).

```bash
.venv/bin/twin run examples/stm32-button-led/scenarios/button_blink.yaml --trace
```

The scenario presses the button at t=2s and asserts, from the LED net's
actual edge timestamps, that the blink period switches 250 ms → 50 ms.
Between SysTick interrupts the CPU sits in WFI and the power engine records
sleep current — interrupt latency, GPIO behavior, and power are all one
timeline.
