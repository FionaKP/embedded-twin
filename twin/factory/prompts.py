"""Prompt builders for the model factory stages."""
from __future__ import annotations

from pathlib import Path

from .spec import BehaviorSpec, PIN_ROLES, FAMILIES, CLAIM_KINDS

_DOCS = Path(__file__).parent.parent.parent / "docs"


def sdk_reference() -> str:
    """The Component SDK contract, included verbatim in drafting prompts."""
    try:
        return (_DOCS / "MODEL_SDK.md").read_text()
    except OSError:
        return "(MODEL_SDK.md unavailable — follow twin.components.base.Component)"


EXTRACT_SYSTEM = f"""\
You are an embedded-systems expert extracting a behavioral specification
from a component datasheet, for use in a board-level digital-twin simulator.

Extract ONLY what the datasheet states. Where you must estimate, say so in
confidence_notes. Numbers (currents, voltages, timings) must come from the
datasheet's electrical characteristics tables — typical values unless only
min/max are given.

Respond with ONE JSON object, no prose, matching:
{{
  "mpn": str, "family": one of {sorted(FAMILIES)}, "description": str,
  "pins": [{{"name": str, "role": one of {sorted(PIN_ROLES)}, "description": str}}],
  "power_states": [{{"name": str, "current_a": float, "rail_pin": str,
                     "conditions": str}}],
  "interfaces": [{{"type": "i2c"|"spi"|"uart"|"gpio", "params": {{...}}}}],
  "behaviors": [str, ...],   // state machine / functional rules, one per line
  "claims": [{{"id": str, "kind": one of {sorted(CLAIM_KINDS)},
               "description": str, "expect": {{...}}}}],
  "params": {{...}},          // user-tunable parameters with defaults
  "confidence_notes": [str, ...]
}}

claims are the datasheet facts a conformance test must verify — quiescent
currents, register defaults (WHO_AM_I etc.), protocol responses, thresholds,
timings. Include at least 3 claims for any active part.
"""


def extract_user(mpn_hint: str) -> str:
    return (f"Extract the behavioral specification for this part"
            f"{f' (MPN hint: {mpn_hint})' if mpn_hint else ''}. "
            "The datasheet follows.")


DRAFT_SYSTEM_TEMPLATE = """\
You are an embedded-systems expert writing a component model for the
embedded-twin simulator, from a behavioral specification extracted from the
part's datasheet.

Here is the Model SDK contract you must follow exactly:

<model_sdk>
{sdk}
</model_sdk>

Additional API facts:
- Register the model as @register("gen.{slug}").
- The generated file must import ONLY from `twin.*` and the standard library.
- The conformance test builds a minimal synthetic board:
    from twin.ir import BoardIR, ComponentIR, NetIR, NetNode
    from twin.build import build_twin
  wires the part's pins to nets, runs the kernel, and asserts EVERY claim
  in the spec (name each test test_claim_<id>). Currents are asserted via
  twin.power.report() rails, protocol behavior via the comm buses
  (twin.comm.I2CBus.on_nets / SpiBus.on_nets / Uart), states via
  component.state, net levels via twin.net(...).level.
- Tests import the model as:  import gen_model  (it lives next to the test).

Respond with ONE JSON object, no prose:
{{"model_py": "<full source>", "test_py": "<full source>"}}
"""


def draft_system(spec: BehaviorSpec) -> str:
    return DRAFT_SYSTEM_TEMPLATE.format(sdk=sdk_reference(), slug=spec.slug())


def draft_user(spec: BehaviorSpec) -> str:
    return f"Write the model and conformance test for this spec:\n\n{spec.to_json()}"


def revise_user(spec: BehaviorSpec, model_py: str, test_py: str,
                failure_output: str) -> str:
    return f"""\
The conformance tests failed. Fix the model (and/or the tests, if a test
mis-encodes a claim — the spec is the authority, not the failing test).

Spec:
{spec.to_json()}

Current gen_model.py:
```python
{model_py}
```

Current test_gen.py:
```python
{test_py}
```

Pytest output:
```
{failure_output[-6000:]}
```

Respond with ONE JSON object, no prose:
{{"model_py": "<full source>", "test_py": "<full source>"}}
"""
