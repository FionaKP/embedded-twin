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
    p_run.add_argument("--trace", action="store_true",
                       help="also write <name>.trace.json for the UI")

    p_view = sub.add_parser("view", help="open a run trace in the browser UI")
    p_view.add_argument("trace", help="a .trace.json produced by `twin run --trace`")
    p_view.add_argument("--port", type=int, default=8321)
    p_view.add_argument("--no-open", action="store_true",
                        help="don't launch a browser")

    sub.add_parser("models", help="list the component model registry")

    p_fac = sub.add_parser("factory",
                           help="generate a component model from a datasheet (agentic)")
    p_fac.add_argument("datasheet", help="datasheet file (.pdf or text)")
    p_fac.add_argument("--mpn", default="", help="part number hint")
    p_fac.add_argument("-o", "--outdir", default="twin_models")
    p_fac.add_argument("--max-iters", type=int, default=3)
    p_fac.add_argument("--llm-model", default=None,
                       help="override the Claude model id")

    args = p.parse_args(argv)
    return {"ingest": _ingest, "run": _run, "models": _models,
            "view": _view, "factory": _factory}[args.cmd](args)


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
    from .scenario import to_markdown
    from .scenario.runner import ScenarioRun
    from .scenario.spec import load_scenario
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    all_passed = True
    for sc_path in args.scenario:
        run = ScenarioRun(load_scenario(sc_path))
        result = run.run()
        all_passed &= result["passed"]
        stem = Path(sc_path).stem
        if args.trace:
            from .scenario.traceexport import export_trace
            (outdir / f"{stem}.trace.json").write_text(
                json.dumps(export_trace(run.twin, result)))
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


def _view(args) -> int:
    import http.server
    import functools
    import shutil
    import threading
    import webbrowser

    ui_dir = Path(__file__).parent.parent / "ui"
    trace = Path(args.trace).resolve()
    if not trace.exists():
        print(f"no such trace: {trace}", file=sys.stderr)
        return 1
    serve_dir = trace.parent / ".twin-view"
    serve_dir.mkdir(exist_ok=True)
    for f in ui_dir.glob("*"):
        if f.is_file():
            shutil.copy(f, serve_dir / f.name)
    shutil.copy(trace, serve_dir / "trace.json")

    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(serve_dir))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://127.0.0.1:{args.port}/index.html"
    print(f"twin view: serving {trace.name} at {url}  (ctrl-c to stop)")
    if not args.no_open:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def _factory(args) -> int:
    try:
        from .factory.llm import ClaudeLLM
    except ImportError:
        print("the factory needs the Anthropic SDK: pip install anthropic",
              file=sys.stderr)
        return 1
    from .factory import ModelFactory
    llm = ClaudeLLM(model=args.llm_model) if args.llm_model else ClaudeLLM()
    result = ModelFactory(llm).run(args.datasheet, mpn_hint=args.mpn,
                                   max_iters=args.max_iters, out_dir=args.outdir)
    if result.passed:
        print(f"PASS after {result.iterations} iteration(s): {result.installed}")
        print("Review the generated model and its conformance tests before use;")
        print(f"load it in scenarios via  board.model_dirs: [{args.outdir}]")
        return 0
    print(f"FAILED conformance after {result.iterations} iteration(s)")
    print(result.test_output[-2000:])
    return 1


def _models(args) -> int:
    from .components import REGISTRY
    for key in sorted(REGISTRY):
        doc = (REGISTRY[key].__doc__ or "").strip().splitlines()[0]
        print(f"{key:24s} {doc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
