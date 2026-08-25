"""twin CLI: ingest boards, run scenarios, inspect the model library."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="twin",
                                description="embedded-twin: run embedded hardware on a PC")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="parse design files into Board IR")
    p_ingest.add_argument("netlist", help="KiCad netlist (.net)")
    p_ingest.add_argument("--bom", help="BOM CSV")
    p_ingest.add_argument("-o", "--out", help="write Board IR JSON here")

    p_run = sub.add_parser("run", help="run a scenario")
    p_run.add_argument("scenario", nargs="+", help="scenario YAML file(s)")
    p_run.add_argument("-o", "--outdir", default="out", help="report directory")
    p_run.add_argument("--json", action="store_true", help="print JSON result")

    sub.add_parser("models", help="list the component model registry")

    args = p.parse_args(argv)
    return {"ingest": _ingest, "run": _run, "models": _models}[args.cmd](args)


def _ingest(args) -> int:
    from .ingest import parse_kicad_netlist, merge_bom, bind_models
    board = parse_kicad_netlist(args.netlist)
    missing = merge_bom(board, args.bom) if args.bom else []
    report = bind_models(board)
    print(f"board: {board.name}  components: {len(board.components)}  "
          f"nets: {len(board.nets)}")
    for how in ("explicit", "by_part_number", "by_refdes"):
        if report[how]:
            print(f"  bound ({how.replace('_', ' ')}): {', '.join(report[how])}")
    if report["unbound"]:
        print(f"  ⚠ UNBOUND (open circuit in sim): {', '.join(report['unbound'])}")
    if missing:
        print(f"  ⚠ in BOM but not netlist: {', '.join(missing)}")
    if args.out:
        Path(args.out).write_text(board.to_json())
        print(f"wrote {args.out}  (sha256 {board.sha256()[:16]}…)")
    return 0


def _run(args) -> int:
    from .scenario import run_scenario, to_markdown
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    all_passed = True
    for sc_path in args.scenario:
        result = run_scenario(sc_path)
        all_passed &= result["passed"]
        stem = Path(sc_path).stem
        (outdir / f"{stem}.report.md").write_text(to_markdown(result))
        (outdir / f"{stem}.result.json").write_text(json.dumps(result, indent=2, default=str))
        (outdir / f"{stem}.lock.json").write_text(json.dumps(result["lock"], indent=2))
        verdict = "PASS" if result["passed"] else "FAIL"
        n_ok = sum(1 for r in result["assertions"] if r["passed"])
        print(f"[{verdict}] {result['scenario']}: {n_ok}/{len(result['assertions'])} "
              f"assertions  ->  {outdir / f'{stem}.report.md'}")
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        if not result["passed"]:
            for r in result["assertions"]:
                if not r["passed"]:
                    print(f"    ❌ {r['spec']['type']}: {r['evidence']}")
    return 0 if all_passed else 1


def _models(args) -> int:
    from .components import REGISTRY
    for key in sorted(REGISTRY):
        doc = (REGISTRY[key].__doc__ or "").strip().splitlines()[0]
        print(f"{key:24s} {doc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
