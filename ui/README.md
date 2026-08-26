# embedded-twin trace viewer

Browser UI for simulation runs: a virtual logic analyzer (waveforms, state
machines, UART/I2C decode) plus power/battery/thermal dashboards, serial
consoles, assertions and logs. Plain HTML + vanilla ES modules + CSS — no
build step, no external dependencies, works fully offline.

Served by `twin view`, which hosts this directory next to a `trace.json`
produced by a run. The `trace.json` schema is the contract defined in
`twin/scenario/traceexport.py`. For manual use: copy a `*.trace.json` next to
these files as `trace.json` and run `python3 -m http.server`.
