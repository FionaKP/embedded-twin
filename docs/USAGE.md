# Using embedded-twin on your own project

## 1. Export your design

From KiCad: **Schematic Editor → File → Export → Netlist (KiCad format, `.net`)**.
Export your BOM as CSV with at least `Reference` and `PartNumber` columns.
Optional columns: `Model` (explicit model key — wins over all heuristics) and
`param:<name>` (e.g. `param:capacity_mah`) to pass parameters to models.

```bash
twin ingest hardware/myboard.net --bom hardware/bom.csv -o board.json
```

Read the bind report carefully. **Unbound components are open circuits in the
sim** — bind them via the BOM `Model` column or extend the part table
(`twin/ingest/binding.py`). `twin models` lists available model keys.

## 2. Describe the world and the test

```yaml
name: my_test
duration: 60s
seed: 1
board:
  netlist: hardware/myboard.net
  bom: hardware/bom.csv
  external_supplies: {VUSB: 5.0}     # bench power, if no battery on board
  components:
    U1:
      params:
        pinmap: {PA0: 0, PA5: 5}     # MCU pin name -> GPIO bit
        uarts: [{tx: PA9, rx: PA10, baud: 115200}]
environment:
  position: {lat: 42.26, lon: -71.80}
  gnss: {condition: urban_canyon}
events:
  - {at: 5s, do: press, target: SW1}
  - {at: 10s, do: set_net, net: NRST, value: low}    # fault injection
assertions:
  - {type: net_level, net: /LED_CTRL, at: 6s, equals: "1"}
  - {type: power_budget, rail: VBAT, max_avg_ma: 20}
```

Run: `twin run my_test.yaml` — exit code 0 only if every assertion holds.

## 3. Bring firmware

**Real binary** (when you have a build): `firmware: {U1: {file: build/app.bin}}`.
The binary must target the TwinMCU memory map (`twin/cpu/memmap.py`) until
vendor memory maps land (roadmap v0.2). Flat `.bin` (vector table first) or ELF.

**Behavioral** (before firmware exists): `firmware: {U1: {behavioral: fw:main}}`
points at a Python function `main(api)` — see `McuApi` in
`twin/components/mcu.py` and the asset-tracker example. Same board, same
scenarios; swap in the real binary later without touching the tests.

## 4. Probe anything

Every net transition, UART byte, I2C transaction, state change, and power
sample is in the trace. In Python:

```python
from twin.scenario import ScenarioRun, load_scenario
run = ScenarioRun(load_scenario("my_test.yaml"))
result = run.run()
trace = run.twin.kernel.trace
trace.transitions("/LED_CTRL")        # [(t_ns, "1"), ...]
trace.uart_bytes("/MDM_TX")           # everything the modem said
trace.net_level_at("/BTN", 5_000_000) # probe an instant
```

## 5. Version everything

Commit netlist, BOM, firmware, and scenarios to git. Every report embeds a
lockfile of content hashes — when a result changes, diff the lock to see
*which input* changed. (ADR-0001.)
