# Deferred capabilities

Judged out-of-scope for the core mission of v0.1 (proof out an already-designed system), recorded here as they came up so they aren't lost. Each entry: what, why deferred, what in v0.1 makes it possible later.

| Capability | Why deferred | v0.1 hook that enables it |
|---|---|---|
| Full SPICE/analog co-sim | System troubleshooting rarely needs transistor-level solve; huge perf cost | Nets already carry analog voltage; engine interface allows an ngspice adapter |
| Cycle-accurate CPU timing | Unicorn is instruction-accurate, not cycle-accurate; enough for logic/power/protocol bugs | `FirmwareBackend` interface — a QEMU/Renode backend can slot in |
| Vendor HAL memory maps (STM32, nRF52, ESP32) | Each map is big; generic map proves the mechanism | Peripheral bus is address-table-driven; maps are data, not code |
| BRD/copper-aware thermal + IR drop | Needs PCB geometry parsing; RC lumps prove the modeling seam | Thermal engine takes a node graph — geometry just generates a denser graph |
| Live TLE / real-time cell-tower databases | Offline determinism first; bundled TLE snapshots suffice | GNSS engine takes any TLE file; a fetcher is a 20-line addition |
| Datasheet-reading agent (model factory) | Needs API-key/runtime decisions + eval harness; SDK contract had to exist first | Component SDK + registry + conformance-test pattern are the contract |
| Web waveform/dashboard UI | CLI + Markdown/JSON reports prove value first | Trace recorder already stores full event history in a queryable form |
| Hardware-in-the-loop bridging | Different mission (real hardware present) | Peripheral bus events could be mirrored to a serial/USB bridge |
| Multi-board / mesh simulation | Single board first | Kernel supports multiple boards in one event queue by construction |
| Fault injection library (bit flips, brownouts, connector intermittents) | Ringer needs to exist first | Stimuli timeline supports arbitrary net forcing — faults are stimuli presets |
