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

## v0.2 — fidelity
- More MCU peripherals (SPI, I2C, ADC, DMA, RTC, low-power modes), NVIC interrupt fidelity
- STM32/nRF52 memory-map profiles so vendor-HAL firmware runs unmodified
- Altium/Eagle netlist ingest; KiCad PCB (.kicad_pcb / BRD) ingest for copper-aware thermal + current paths
- ngspice co-simulation engine for analog subcircuits

## v0.3 — the agentic model factory
- Datasheet → component model pipeline: an agent reads a part's datasheet, drafts a model against the SDK, generates conformance tests from datasheet tables, and iterates until they pass
- Model provenance + confidence grading (datasheet-verified vs. heuristic)
- Community model library with versioned releases

## v0.4 — team workflows
- Web UI: waveform viewer, power dashboards, board schematic overlay of live sim state
- CI integration: run the ringer on every firmware PR / schematic rev
- Expert embedded-systems agent for ideation-stage projects (design review against the twin)
