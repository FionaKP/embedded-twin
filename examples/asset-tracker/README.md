# Example: GNSS asset tracker

A realistic battery-powered tracker, defined the way a real project is:
a KiCad netlist + BOM ([hardware/](hardware/)), firmware
([firmware/tracker_fw.py](firmware/tracker_fw.py)), and a set of ringer
scenarios ([scenarios/](scenarios/)).

**The board:** 18650 cell → AP2112 LDO → 3.3 V rail. STM32F405 MCU,
NEO-M8N GNSS (duty-cycled via EN), SARA-R5 cellular modem (powered straight
from VBAT, as real designs do), RN4870 BLE, TMP102 temp sensor, status LED,
user button.

**The firmware:** wakes every 60 s, powers the GNSS, reads temperature over
I2C, waits for fix + network, sends a position report over AT commands,
blinks the LED, powers the GNSS down, sleeps.

## Run it

```bash
.venv/bin/twin run examples/asset-tracker/scenarios/smoke_test.yaml -o examples/asset-tracker/out
```

| scenario | what it proves |
|---|---|
| `smoke_test` | whole system comes up: fix in ~28 s (cold TTFF), network registration, one report sent, LED blink observed on the actual net |
| `battery_life_24h` | 24 h duty-cycled operation: avg/peak per rail, battery SoC, LDO temperature — simulated in ~2 s of wall time |
| `urban_canyon_recovery` | GNSS starved in a street canyon (3 usable SVs); firmware skips reports cleanly and recovers with a hot start |
| `network_congestion` | tower load spikes; modem is kicked off (+CREG: 0 URC), re-registers, reporting resumes |

Every run writes a Markdown report with assertion evidence, per-rail power,
thermal results, and a content-hash lockfile pinning the exact design inputs.
