"""Assertion engine: interrogate the trace and power report, return verdicts.

Every verdict carries evidence — the actual value seen and where/when — so a
failing report reads like a probe capture, not a shrug.
"""
from __future__ import annotations

from typing import Any

from ..build import BoardTwin
from .spec import parse_time


def evaluate(specs: list[dict], twin: BoardTwin, power: dict) -> list[dict]:
    out = []
    for spec in specs:
        kind = spec["type"]
        fn = _HANDLERS.get(kind)
        if fn is None:
            out.append(_verdict(spec, False, f"unknown assertion type {kind!r}"))
            continue
        try:
            out.append(fn(spec, twin, power))
        except Exception as e:  # an assertion crashing is itself a failure
            out.append(_verdict(spec, False, f"error evaluating: {e}"))
    return out


def _verdict(spec: dict, passed: bool, evidence: str) -> dict:
    return {"spec": spec, "passed": passed, "evidence": evidence}


def _net_level(spec, twin, power):
    t = parse_time(spec["at"])
    level = twin.kernel.trace.net_level_at(spec["net"], t)
    want = str(spec["equals"])
    return _verdict(spec, level == want,
                    f"net {spec['net']} = {level!r} at {spec['at']} (want {want!r})")


def _uart_contains(spec, twin, power):
    start = parse_time(spec.get("after", 0))
    end = parse_time(spec["before"]) if "before" in spec else None
    data = twin.kernel.trace.uart_bytes(spec["net"], start=start, end=end)
    pat = spec["pattern"].encode()
    ok = pat in data
    window = f" in [{spec.get('after', '0s')}..{spec.get('before', 'end')}]"
    return _verdict(spec, ok,
                    f"{spec['pattern']!r} {'found' if ok else 'NOT found'} on "
                    f"{spec['net']}{window} ({len(data)} bytes seen)")


def _uart_not_contains(spec, twin, power):
    v = _uart_contains(spec, twin, power)
    return _verdict(spec, not v["passed"], v["evidence"].replace("found", "present"))


def _power_budget(spec, twin, power):
    rail = power["rails"].get(spec["rail"])
    if rail is None:
        return _verdict(spec, False, f"rail {spec['rail']} has no accounting")
    checks = []
    ok = True
    for key, label in (("max_avg_ma", "avg_ma"), ("max_peak_ma", "peak_ma"),
                       ("max_mah", "charge_mah")):
        if key in spec:
            actual = rail[label]
            good = actual <= spec[key]
            ok &= good
            checks.append(f"{label}={actual:.2f} (limit {spec[key]})")
    return _verdict(spec, ok, f"rail {spec['rail']}: " + ", ".join(checks))


def _battery_soc_min(spec, twin, power):
    for b in power["batteries"]:
        if b["ref"] == spec["ref"]:
            ok = b["soc"] >= spec["min"]
            return _verdict(spec, ok, f"{spec['ref']} SoC={b['soc']:.3f} "
                                      f"(min {spec['min']}), V={b['voltage']}")
    return _verdict(spec, False, f"no battery {spec['ref']!r}")


def _state_reached(spec, twin, power):
    by = parse_time(spec["by"]) if "by" in spec else None
    for e in twin.kernel.trace.select(kind="state", name=spec["ref"]):
        if e.value == spec["state"] and (by is None or e.time <= by):
            return _verdict(spec, True,
                            f"{spec['ref']} reached {spec['state']!r} at {e.time / 1e9:.3f}s")
    seen = {e.value for e in twin.kernel.trace.select(kind="state", name=spec["ref"])}
    return _verdict(spec, False,
                    f"{spec['ref']} never reached {spec['state']!r}"
                    + (f" by {spec['by']}" if by else "") + f"; saw {sorted(seen)}")


def _state_at_end(spec, twin, power):
    comp = twin.components.get(spec["ref"])
    if comp is None:
        return _verdict(spec, False, f"no component {spec['ref']!r}")
    ok = comp.state == spec["state"]
    return _verdict(spec, ok, f"{spec['ref']} ended in {comp.state!r} (want {spec['state']!r})")


def _no_contention(spec, twin, power):
    hits = [e for e in twin.kernel.trace.select(kind="net")
            if e.value.get("contention")]
    if not hits:
        return _verdict(spec, True, "no bus contention on any net")
    first = hits[0]
    return _verdict(spec, False,
                    f"contention on {len(set(h.name for h in hits))} net(s); first: "
                    f"{first.name} at {first.time / 1e9:.6f}s")


def _temp_max(spec, twin, power):
    t = power["thermal"].get(spec["ref"])
    if t is None:
        return _verdict(spec, False, f"no thermal node for {spec['ref']!r}")
    ok = t <= spec["max_c"]
    return _verdict(spec, ok, f"{spec['ref']} final temp {t}°C (max {spec['max_c']}°C)")


def _log_contains(spec, twin, power):
    pat = spec["pattern"]
    for _t, name, v in twin.kernel.trace.logs():
        if pat in str(v) and (spec.get("ref") in (None, name)):
            return _verdict(spec, True, f"log from {name}: {v!r}")
    return _verdict(spec, False, f"no log matching {pat!r}")


def _transition_count(spec, twin, power):
    edges = twin.kernel.trace.transitions(spec["net"])
    if "level" in spec:
        edges = [e for e in edges if e[1] == str(spec["level"])]
    n = len(edges)
    lo, hi = spec.get("min", 0), spec.get("max", 10 ** 9)
    return _verdict(spec, lo <= n <= hi,
                    f"{spec['net']}: {n} transitions (want {lo}..{hi})")


_HANDLERS = {
    "net_level": _net_level,
    "uart_contains": _uart_contains,
    "uart_not_contains": _uart_not_contains,
    "power_budget": _power_budget,
    "battery_soc_min": _battery_soc_min,
    "state_reached": _state_reached,
    "state_at_end": _state_at_end,
    "no_contention": _no_contention,
    "temp_max": _temp_max,
    "log_contains": _log_contains,
    "transition_count": _transition_count,
}
