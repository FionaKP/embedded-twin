# Roadmap

## v0.1 — the proving ground (complete)
- [x] Architecture + ADRs
- [x] Core kernel: event scheduler, nets, signal resolution, trace recorder
- [x] Board IR + JSON serialization
- [x] Ingest: KiCad netlist (S-expr) + BOM CSV → IR
- [x] Component model SDK + standard library (regulator, battery, LED, button, sensor, GNSS, cell modem, BLE)
- [x] Power + thermal engine
- [x] CPU: Unicorn Cortex-M backend (GPIO, USART, SysTick) + behavioral firmware backend
- [x] Environment: GNSS (SGP4/TLE), cellular, BLE
- [x] Scenario engine + CLI + reports + lockfile
- [x] Example project: asset-tracker board with ringer scenarios
- [x] CI (GitHub Actions)

## v0.2 — fidelity (in progress)
- [x] Vendor-profile framework: address-table peripherals + ARMv7-M system
  layer (SysTick, banked NVIC, SCB, exception entry/return, WFI-as-sleep)
- [x] STM32F4 profile: RCC/FLASH/PWR/GPIOA-E/USART1-2-6 at real addresses;
  register-level C firmware runs unmodified (zig-compiled, pip-installable
  toolchain)
- [x] Run viewer UI: waveforms, power dashboards, serial consoles (twin view)
- [x] nRF52 profile: CLOCK, GPIO P0, UARTE0 with EasyDMA against emulated RAM
- [ ] More STM32 peripherals: SPI, I2C, ADC, DMA, RTC, EXTI, TIM
- [ ] Exception preemption/nesting + SVC/PendSV (unlocks FreeRTOS)
- [ ] Altium/Eagle netlist ingest; KiCad PCB (.kicad_pcb) for copper-aware
  thermal + current paths
- [ ] ngspice co-simulation engine for analog subcircuits

## v0.3 — the agentic model factory
- Datasheet → component model pipeline: an agent reads a part's datasheet, drafts a model against the SDK, generates conformance tests from datasheet tables, and iterates until they pass
- Model provenance + confidence grading (datasheet-verified vs. heuristic)
- Community model library with versioned releases

## v0.4 — team workflows
- Web UI: waveform viewer, power dashboards, board schematic overlay of live sim state
- CI integration: run the ringer on every firmware PR / schematic rev
- Expert embedded-systems agent for ideation-stage projects (design review against the twin)
