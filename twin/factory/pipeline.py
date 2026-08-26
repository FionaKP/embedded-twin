"""The model factory pipeline: datasheet -> spec -> model + conformance
tests -> iterate until green -> install.

Generated code is executed only inside its conformance-test run (a pytest
subprocess) and is installed for review, never silently registered — see
docs/FACTORY.md for the trust model.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import prompts
from .llm import datasheet_content, parse_json_reply
from .spec import BehaviorSpec


@dataclass
class FactoryResult:
    spec: BehaviorSpec
    model_code: str = ""
    test_code: str = ""
    iterations: int = 0
    passed: bool = False
    test_output: str = ""
    installed: Path | None = None


class ModelFactory:
    def __init__(self, llm, log=print):
        self.llm = llm
        self.log = log

    # -- stages -----------------------------------------------------------
    def extract(self, datasheet_path: str, mpn_hint: str = "") -> BehaviorSpec:
        self.log(f"factory: extracting spec from {datasheet_path}")
        content = datasheet_content(datasheet_path)
        content.insert(0, {"type": "text", "text": prompts.extract_user(mpn_hint)})
        reply = self.llm.complete(prompts.EXTRACT_SYSTEM, content)
        spec = BehaviorSpec(**parse_json_reply(reply))
        problems = spec.validate()
        if problems:
            raise ValueError(f"extracted spec is invalid: {problems}")
        return spec

    def draft(self, spec: BehaviorSpec) -> tuple[str, str]:
        self.log(f"factory: drafting model for {spec.mpn}")
        reply = self.llm.complete(prompts.draft_system(spec),
                                  prompts.draft_user(spec))
        out = parse_json_reply(reply)
        return out["model_py"], out["test_py"]

    def revise(self, spec: BehaviorSpec, model_py: str, test_py: str,
               failure: str) -> tuple[str, str]:
        self.log("factory: revising after conformance failures")
        reply = self.llm.complete(prompts.draft_system(spec),
                                  prompts.revise_user(spec, model_py, test_py,
                                                      failure))
        out = parse_json_reply(reply)
        return out["model_py"], out["test_py"]

    def conformance(self, model_py: str, test_py: str) -> tuple[bool, str]:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "gen_model.py").write_text(model_py)
            (td / "test_gen.py").write_text(test_py)
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", str(td), "-q", "--no-header",
                 "-x", "--timeout=120"] if _has_timeout() else
                [sys.executable, "-m", "pytest", str(td), "-q", "--no-header", "-x"],
                capture_output=True, text=True, timeout=300)
            return proc.returncode == 0, proc.stdout + proc.stderr

    # -- orchestration ----------------------------------------------------
    def run(self, datasheet_path: str, mpn_hint: str = "",
            max_iters: int = 3, out_dir: str | Path = "twin_models") -> FactoryResult:
        spec = self.extract(datasheet_path, mpn_hint)
        result = FactoryResult(spec=spec)
        model_py, test_py = self.draft(spec)
        for i in range(1, max_iters + 1):
            result.iterations = i
            passed, output = self.conformance(model_py, test_py)
            result.model_code, result.test_code = model_py, test_py
            result.test_output = output
            if passed:
                result.passed = True
                break
            self.log(f"factory: iteration {i} failed conformance")
            if i < max_iters:
                model_py, test_py = self.revise(spec, model_py, test_py, output)
        if result.passed:
            result.installed = self.install(spec, model_py, test_py, out_dir)
        return result

    def install(self, spec: BehaviorSpec, model_py: str, test_py: str,
                out_dir: str | Path) -> Path:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        slug = spec.slug()
        (out / f"{slug}.py").write_text(model_py)
        (out / f"test_{slug}.py").write_text(test_py)
        (out / f"{slug}.spec.json").write_text(spec.to_json())
        self.log(f"factory: installed gen.{slug} -> {out}/ "
                 f"(review before trusting; conformance tests included)")
        return out / f"{slug}.py"


def _has_timeout() -> bool:
    try:
        import pytest_timeout  # noqa: F401
        return True
    except ImportError:
        return False
