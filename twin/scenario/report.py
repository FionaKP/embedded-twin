"""Render a run result as Markdown."""
from __future__ import annotations


def to_markdown(result: dict) -> str:
    lines = [f"# Scenario: {result['scenario']}",
             "",
             f"**Verdict: {'PASS' if result['passed'] else 'FAIL'}**  ·  "
             f"duration {result['duration']}  ·  seed {result['seed']}  ·  "
             f"lock `{result['lock']['lock_hash']}`",
             ""]

    lines += ["## Assertions", "", "| # | result | type | evidence |",
              "|---|--------|------|----------|"]
    for i, r in enumerate(result["assertions"], 1):
        mark = "✅" if r["passed"] else "❌"
        lines.append(f"| {i} | {mark} | {r['spec']['type']} | {r['evidence']} |")

    p = result["power"]
    lines += ["", "## Power", "",
              f"Simulated: {p['sim_hours']:.3f} h",
              "", "| rail | avg mA | peak mA | mAh | mWh |",
              "|------|--------|---------|-----|-----|"]
    for rail, s in sorted(p["rails"].items()):
        lines.append(f"| {rail} | {s['avg_ma']:.3f} | {s['peak_ma']:.1f} "
                     f"| {s['charge_mah']:.2f} | {s['energy_mwh']:.2f} |")
    for b in p["batteries"]:
        est = f", est. runtime {b['est_runtime_h']} h" if b.get("est_runtime_h") else ""
        lines.append(f"\n**Battery {b['ref']}**: SoC {b['soc'] * 100:.1f} %, "
                     f"{b['voltage']} V{est}")
    if p["thermal"]:
        lines += ["", "| component | final temp °C |", "|-----------|---------------|"]
        for ref, t in sorted(p["thermal"].items()):
            lines.append(f"| {ref} | {t} |")

    if result["build_warnings"]:
        lines += ["", "## Build warnings", ""]
        lines += [f"- {w}" for w in result["build_warnings"]]

    lines += ["", "## Traceability", "", "```json"]
    import json
    lines.append(json.dumps(result["lock"], indent=2))
    lines += ["```", ""]
    return "\n".join(lines)
