# ADR-0003: Unicorn engine for CPU emulation (QEMU/Renode as future backends)

**Status:** accepted

## Context
Running real firmware needs a CPU emulator. Candidates: QEMU (fast, cycle-approximate, heavyweight integration), Renode (purpose-built embedded system sim, .NET runtime, own ecosystem), Unicorn (QEMU's CPU core as a library, per-instruction hooks, pip-installable), or writing an interpreter (madness).

## Decision
Unicorn engine behind a `FirmwareBackend` interface. It is instruction-accurate ARM (M-class mode), embeds cleanly in our Python event loop, gives memory hooks for peripheral dispatch, and installs from pip on every platform.

## Consequences
- Peripherals live on our side as Python models sharing the event kernel with the rest of the board — pin behavior, power states, and firmware interact in one timeline (Renode/QEMU would wall firmware off in their own device models).
- Timing is cycle-approximate (configurable instructions-per-second), not cycle-accurate; fine for logic/protocol/power work, noted in reports.
- A Renode or QEMU backend can be added behind the same interface when cycle fidelity matters.
