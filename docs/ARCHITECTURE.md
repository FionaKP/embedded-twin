# Architecture

## Design philosophy

1. **The Board IR is the center of gravity.** Every design input (netlist, BOM, firmware, datasheet-derived models) compiles down to one canonical, versionable representation. Every simulator consumes it. New input formats = new ingest frontends; new physics = new engines. Neither touches the other.
2. **Behavior over structure.** A SPICE-accurate analog solve of a whole board is neither tractable nor needed for system troubleshooting. We model components *behaviorally* (state machines, transfer functions, current-draw states) at event resolution, with analog values carried where they matter (rails, ADC inputs). Full analog co-sim (ngspice) is a deferred engine, not a rewrite.
3. **Real firmware when you have it, behavioral firmware when you don't.** The same board twin runs either an ELF on an emulated Cortex-M or a Python behavioral firmware — so proofing can start at ideation and tighten as the design matures.
4. **Determinism.** Same inputs → same trace, always. All randomness flows from a seeded RNG owned by the scenario. This is what makes "put it through the ringer" meaningful.

## Layers

```
┌────────────────────────────────────────────────────────┐
│ scenario engine  (YAML scenarios, assertions, reports) │
├────────────────────────────────────────────────────────┤
│ environment      (GNSS · cellular · BLE · user input)  │
├──────────────┬──────────────┬──────────────────────────┤
│ cpu/firmware │ component    │ power + thermal          │
│ (Unicorn /   │ models       │ (state currents, rails,  │
│  behavioral) │ (model SDK)  │  battery SoC, RC thermal)│
├──────────────┴──────────────┴──────────────────────────┤
│ core kernel   (event scheduler, nets, signal resolve)  │
├────────────────────────────────────────────────────────┤
│ Board IR      (components · pins · nets · rails)       │
├────────────────────────────────────────────────────────┤
│ ingest        (KiCad netlist, BOM CSV → IR)            │
└────────────────────────────────────────────────────────┘
```

### Core kernel (`twin/core`)
Discrete-event simulation. Time is an integer in **nanoseconds**. A heap-based scheduler dispatches events; components schedule callbacks and react to net changes. Nets resolve multiple drivers by strength (`STRONG > PULL > HIZ`), carry digital level (0/1/Z/X) and optionally an analog voltage. A trace recorder captures every net transition and component state change for post-run analysis — this is the "virtual probe."

### Board IR (`twin/ir`)
Plain dataclasses, JSON-serializable. `Board` → `Component` (refdes, part number, model binding, params) → `Pin` (name, number, electrical role) and `Net` (connected pins, net class: signal/power/ground). The IR carries *structure*; behavior comes from the model library binding (`Component.model` → registry key).

### Ingest (`twin/ingest`)
Parses KiCad netlist exports (S-expression format) into the IR; merges BOM CSV for part numbers/values. Part-number → model binding uses a mapping table plus heuristics (refdes prefix, value parsing). Unbound components get a `passive` or `stub` model and are reported, never silently dropped.

### Component model SDK (`twin/components`)
A model subclasses `Component`, declares pins, power states (name → current draw @ rail), and behavior via event handlers (`on_net_change`, scheduled callbacks, bus transactions). A registry maps model keys (e.g. `regulator.ldo`, `gnss.generic_nmea`) to classes. This SDK is the contract that future agent-generated, datasheet-derived models plug into.

### CPU / firmware (`twin/cpu`)
Two interchangeable backends behind one `FirmwareBackend` interface:
- **UnicornMCU**: executes real ARM Cortex-M code (ELF or raw bin). Memory-mapped peripheral bus dispatches loads/stores in peripheral address space to Python peripheral models (GPIO, USART, SysTick). Emulation advances in time slices tied to kernel time via a configurable cycles-per-second clock.
- **BehavioralFirmware**: Python coroutine-style firmware using the same peripheral API — for boards whose firmware doesn't exist yet.

### Power + thermal (`twin/power`)
Each component exposes a current draw for its current power state on each rail it touches. The power engine samples/integrates rail currents over sim time → energy, average/peak draw, battery SoC (simple voltage-vs-SoC curve + internal resistance). Thermal: lumped RC network (component dissipation → node temperature vs. ambient), enough to flag "this LDO runs hot at this duty cycle."

### Environment (`twin/env`)
- **GNSS**: SGP4 propagates real TLE ephemerides → satellite az/el/visibility from scenario lat/lon/time; a condition model (open sky, urban canyon, indoor) degrades C/N0 and fix availability; the `gnss.generic_nmea` component model emits NMEA (GGA/RMC/GSV) on its UART accordingly, with realistic TTFF behavior.
- **Cellular**: path-loss + tower load model → RSSI/RSRP; registration state machine (search → attach → registered → dropped) with load-shedding kickoffs; the modem component model speaks a minimal AT command set.
- **BLE**: central/peripheral connection lifecycle, connection intervals, supervision timeout, interference-driven packet loss.

### Scenario engine (`twin/scenario`)
YAML in: board ref, firmware ref, environment config, stimuli timeline (button presses, rail glitches, temperature steps, RF condition changes), assertions (pin level at time, UART expects, power budget ceilings, state reached). Out: pass/fail per assertion, JSON result, Markdown report, and the full trace for drill-down.

## Versioning & traceability (ADR-0001)
Design inputs evolve independently (schematic revs, firmware builds, model library updates). Every run writes `twin.lock.json`: SHA-256 of each input artifact + model library version + scenario hash. Reports embed the lock, so any result is reproducible and attributable to an exact design state. Git remains the versioning substrate; we do not invent a VCS.
