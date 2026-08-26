"""Model-factory pipeline tests — fully offline via an injected fake LLM.

The fake returns canned artifacts for a fictional 'EX9000' 3.0 V LDO; the
pipeline machinery (spec validation, conformance subprocess, revise loop,
install) is exercised for real, including actually running the generated
conformance tests against the simulator.
"""
import json
from pathlib import Path

import pytest

from twin.factory import BehaviorSpec, ModelFactory, load_model_dir
from twin.factory.llm import parse_json_reply

SPEC = {
    "mpn": "EX9000",
    "family": "regulator",
    "description": "3.0 V 150 mA LDO with enable",
    "pins": [
        {"name": "VIN", "role": "power_in", "description": "supply input"},
        {"name": "VOUT", "role": "power_out", "description": "3.0 V output"},
        {"name": "GND", "role": "gnd", "description": "ground"},
        {"name": "EN", "role": "enable", "description": "active-high enable"},
    ],
    "power_states": [
        {"name": "regulating", "current_a": 55e-6, "rail_pin": "VIN",
         "conditions": "EN high, no load"},
        {"name": "shutdown", "current_a": 1e-6, "rail_pin": "VIN",
         "conditions": "EN low"},
    ],
    "interfaces": [],
    "behaviors": ["VOUT regulates to 3.0 V when VIN present and EN high",
                  "VOUT high-impedance when EN low"],
    "claims": [
        {"id": "c1", "kind": "current",
         "description": "quiescent current 55 uA typ at no load",
         "expect": {"rail": "VIN", "avg_ma": 0.055, "tol_ma": 0.01}},
        {"id": "c2", "kind": "voltage",
         "description": "output voltage 3.0 V",
         "expect": {"net": "VOUT", "volts": 3.0}},
        {"id": "c3", "kind": "state",
         "description": "EN low disables the output",
         "expect": {"state": "off"}},
    ],
    "params": {"vout": 3.0},
    "confidence_notes": [],
}

MODEL_OK = '''\
from twin.components.base import Component, register
from twin.core import Drive


@register("gen.ex9000")
class Ex9000(Component):
    """EX9000 3.0 V LDO (generated from datasheet spec)."""

    def start(self):
        self.vin = self.require_net("VIN")
        self.vout = self.require_net("VOUT")
        self.en = self.net("EN")
        self.vin.listen(lambda _n: self._update())
        if self.en is not None:
            self.en.listen(lambda _n: self._update())
        self.set_state("off")
        self._update()

    def _update(self):
        on = self.vin.is_high and (self.en is None or not self.en.is_low)
        if on:
            self.vout.drive(f"{self.ref}.VOUT", Drive.high(VOUT_V))
            self.set_load(self.vin.name, 55e-6)
            self.set_state("regulating")
        else:
            self.vout.drive(f"{self.ref}.VOUT", Drive.release())
            self.set_load(self.vin.name, 1e-6)
            self.set_state("off")

VOUT_V = 3.0
'''

MODEL_BROKEN = MODEL_OK.replace("VOUT_V = 3.0", "VOUT_V = 3.3")  # wrong vout

TEST_CODE = '''\
from twin.build import build_twin
from twin.core import Drive
from twin.core.kernel import SEC
from twin.ir import BoardIR, ComponentIR, NetIR, NetNode
import gen_model  # noqa: F401  (registers gen.ex9000)


def make_twin():
    b = BoardIR(name="ex9000_rig")
    b.add_component(ComponentIR(ref="U1", model="gen.ex9000",
                                pins={"1": "VIN", "2": "VOUT",
                                      "3": "GND", "4": "EN"}))
    b.add_net(NetIR("VIN", "power", [NetNode("U1", "1")]))
    b.add_net(NetIR("VOUT", "power", [NetNode("U1", "2")]))
    b.add_net(NetIR("GND", "ground", [NetNode("U1", "3")]))
    b.add_net(NetIR("EN", "signal", [NetNode("U1", "4")]))
    twin = build_twin(b, external_supplies={"VIN": 5.0})
    twin.net("EN").drive("test", Drive.high(5.0))
    twin.start()
    return twin


def test_claim_c1_quiescent_current():
    twin = make_twin()
    twin.kernel.run_until(10 * SEC)
    report = twin.power.report()
    assert abs(report["rails"]["VIN"]["avg_ma"] - 0.055) < 0.01


def test_claim_c2_output_voltage():
    twin = make_twin()
    twin.kernel.run()
    assert twin.net("VOUT").voltage == 3.0


def test_claim_c3_enable_low_disables():
    twin = make_twin()
    twin.kernel.run()
    twin.net("EN").drive("test", Drive.low())
    twin.kernel.run()
    assert twin.comp("U1").state == "off"
    assert twin.net("VOUT").level.value == "Z"
'''


class FakeLLM:
    """Stage-aware canned responses; optionally broken first draft."""

    def __init__(self, first_draft_broken: bool = False):
        self.first_draft_broken = first_draft_broken
        self.calls = []

    def complete(self, system: str, user_content, max_tokens: int = 0) -> str:
        if "extracting a behavioral specification" in system:
            self.calls.append("extract")
            return json.dumps(SPEC)
        user_text = user_content if isinstance(user_content, str) else \
            " ".join(b.get("text", "") for b in user_content)
        if "tests failed" in user_text:
            self.calls.append("revise")
            return json.dumps({"model_py": MODEL_OK, "test_py": TEST_CODE})
        self.calls.append("draft")
        model = MODEL_BROKEN if self.first_draft_broken else MODEL_OK
        return json.dumps({"model_py": model, "test_py": TEST_CODE})


def test_spec_validation_catches_problems():
    bad = BehaviorSpec(mpn="", family="widget", description="x")
    problems = bad.validate()
    assert any("mpn" in p for p in problems)
    assert any("family" in p for p in problems)
    assert any("claim" in p for p in problems)
    good = BehaviorSpec(**SPEC)
    assert good.validate() == []
    assert good.slug() == "ex9000"


def test_parse_json_reply_tolerates_fences():
    assert parse_json_reply('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_reply('Here you go: {"a": 1}') == {"a": 1}


def test_pipeline_first_try_green(tmp_path):
    ds = tmp_path / "ex9000.txt"
    ds.write_text("EX9000 fictional datasheet text")
    factory = ModelFactory(FakeLLM(), log=lambda m: None)
    result = factory.run(str(ds), out_dir=tmp_path / "models")
    assert result.passed, result.test_output
    assert result.iterations == 1
    assert (tmp_path / "models" / "ex9000.py").exists()
    assert (tmp_path / "models" / "ex9000.spec.json").exists()


def test_pipeline_revise_loop_fixes_broken_draft(tmp_path):
    ds = tmp_path / "ex9000.txt"
    ds.write_text("EX9000 fictional datasheet text")
    fake = FakeLLM(first_draft_broken=True)
    factory = ModelFactory(fake, log=lambda m: None)
    result = factory.run(str(ds), out_dir=tmp_path / "models")
    assert result.passed, result.test_output
    assert result.iterations == 2
    assert fake.calls == ["extract", "draft", "revise"]


def test_generated_models_load_into_registry(tmp_path):
    (tmp_path / "ex9000.py").write_text(MODEL_OK)
    load_model_dir(tmp_path)
    from twin.components import REGISTRY
    assert "gen.ex9000" in REGISTRY
