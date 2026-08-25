# embedded-twin

**Run your embedded hardware project entirely on a PC.**

embedded-twin builds a *digital twin* of an embedded system from its real design files — schematic/netlist, BOM, and firmware — and simulates the whole system: CPU executing the actual firmware image, pin-level signal behavior, power draw over time, thermal behavior, and the physical environment the device lives in (GNSS constellations, cellular networks, BLE links).

The mission: **lower the technical barrier for embedded teams** by making it possible to troubleshoot a board to probe-on-the-pad detail — without the hardware on your desk.

## What it does today

- **Ingest** a KiCad netlist + BOM into a canonical Board IR (intermediate representation)
- **Simulate** the board with a discrete-event kernel: nets resolve multi-driver conflicts, pull-ups, hi-Z, analog levels
- **Execute firmware** two ways:
  - real ARM Cortex-M binaries via the Unicorn engine, with memory-mapped peripherals (GPIO, UART, SysTick)
  - behavioral firmware written in Python, for pre-silicon / pre-firmware proofing
- **Account for power**: state-based current models per component, rail accounting, battery state-of-charge, and a thermal RC model
- **Simulate the environment**:
  - GNSS: real satellite constellations propagated from TLE data (SGP4), visibility from any lat/lon/time, signal conditions (open sky / urban canyon / indoor), NMEA output from a modeled receiver
  - Cellular: signal strength, network load, registration/attach state machine, dropouts
  - BLE: connection lifecycle, supervision timeouts, interference
- **Run scenarios**: YAML-defined test scenarios ("the testing ringer") with stimuli timelines and assertions on pins, UART traffic, power budgets, and state — producing JSON + Markdown reports
- **Trace to design revision**: every run records a lockfile of input hashes (netlist, firmware, models) so results map to an exact design state

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/twin run examples/asset-tracker/scenarios/battery_life_24h.yaml
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the system design, [docs/ROADMAP.md](docs/ROADMAP.md) for the build phases, and [docs/FUTURE.md](docs/FUTURE.md) for capabilities deliberately deferred.

## Repository layout

```
twin/            the simulation platform (Python package)
  core/          discrete-event kernel, nets, signals, tracing
  ir/            Board IR: components, pins, nets, rails
  ingest/        design-file parsers (KiCad netlist, BOM CSV)
  components/    component model SDK + standard model library
  cpu/           firmware execution (Unicorn ARM backend, behavioral backend)
  power/         power + thermal engine
  env/           environment simulators (GNSS, cellular, BLE)
  scenario/      scenario engine, assertions, reports
docs/            architecture, roadmap, ADRs
examples/        practice projects (asset-tracker demo board)
tests/           pytest suite
```
