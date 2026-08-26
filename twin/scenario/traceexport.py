"""Export a run's full trace as the UI-facing JSON document.

Format (consumed by ui/ and any external tooling — treat as a contract):

{
  "meta":    {scenario, verdict, duration_ns, seed, lock_hash, sim_hours},
  "nets":    [{"name": str, "class": "signal|power|ground"}],
  "signals": {net: [[t_ns, "0"|"1"|"Z"|"X"], ...]},        # digital edges
  "analog":  {net: [[t_ns, volts], ...]},                  # when known
  "uart":    {net: [[t_ns, byte], ...]},
  "i2c":     {net: [[t_ns, {op,addr,data,ack}], ...]},
  "states":  {ref_or_ref.power: [[t_ns, state], ...]},
  "logs":    [[t_ns, source, message], ...],
  "power":   {"rails":   {rail: [[t_ns, mA], ...]},
              "battery": {ref: [[t_ns, soc, volts], ...]},
              "thermal": {ref: [[t_ns, degC], ...]}},
  "events":  [[t_ns, kind, detail], ...],                  # scenario stimuli
  "assertions": [{"type", "passed", "evidence"}, ...],
  "components": [{"ref", "model", "value"}, ...]
}
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..build import BoardTwin


def export_trace(twin: BoardTwin, result: dict) -> dict[str, Any]:
    trace = twin.kernel.trace
    signals: dict[str, list] = defaultdict(list)
    analog: dict[str, list] = defaultdict(list)
    uart: dict[str, list] = defaultdict(list)
    i2c: dict[str, list] = defaultdict(list)
    states: dict[str, list] = defaultdict(list)
    rails: dict[str, list] = defaultdict(list)
    battery: dict[str, list] = defaultdict(list)
    thermal: dict[str, list] = defaultdict(list)
    logs: list = []
    events: list = []

    for e in trace.entries:
        if e.kind == "net":
            signals[e.name].append([e.time, e.value["level"]])
            if e.value.get("v") is not None:
                analog[e.name].append([e.time, e.value["v"]])
        elif e.kind == "uart":
            uart[e.name].append([e.time, e.value])
        elif e.kind == "i2c":
            i2c[e.name].append([e.time, e.value])
        elif e.kind == "state":
            states[e.name].append([e.time, e.value])
        elif e.kind == "rail":
            rails[e.name].append([e.time, e.value])
        elif e.kind == "battery":
            battery[e.name].append([e.time, e.value["soc"], e.value["v"]])
        elif e.kind == "thermal":
            thermal[e.name].append([e.time, e.value])
        elif e.kind == "log":
            logs.append([e.time, e.name, str(e.value)])
        elif e.kind == "event":
            events.append([e.time, e.name, e.value])

    return {
        "meta": {
            "scenario": result["scenario"],
            "verdict": "PASS" if result["passed"] else "FAIL",
            "duration_ns": max((e.time for e in trace.entries), default=0),
            "seed": result["seed"],
            "lock_hash": result["lock"]["lock_hash"],
            "sim_hours": result["power"]["sim_hours"],
        },
        "nets": [{"name": n.name, "class": n.net_class}
                 for n in twin.nets.values()],
        "signals": dict(signals),
        "analog": dict(analog),
        "uart": dict(uart),
        "i2c": dict(i2c),
        "states": dict(states),
        "logs": logs,
        "power": {"rails": dict(rails), "battery": dict(battery),
                  "thermal": dict(thermal)},
        "events": events,
        "assertions": [{"type": r["spec"]["type"], "passed": r["passed"],
                        "evidence": r["evidence"]} for r in result["assertions"]],
        "components": [{"ref": c.ref, "model": c.model, "value": c.value}
                       for c in twin.ir.components.values()],
    }
