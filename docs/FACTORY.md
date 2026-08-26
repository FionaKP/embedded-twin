# The model factory (v0.3 groundwork)

`twin factory <datasheet.pdf> --mpn <part>` turns a datasheet into a
component model using Claude (Anthropic SDK, `claude-opus-5`):

```
datasheet ──extract──▶ BehaviorSpec (JSON) ──draft──▶ model.py + conformance tests
                                                  │
                        revise (≤ --max-iters) ◀──┤ pytest subprocess
                                                  ▼
                              twin_models/<slug>.py + test_<slug>.py + <slug>.spec.json
```

- **The spec is the reviewable artifact**: pins, power states, interfaces,
  behaviors, and — critically — `claims`: the datasheet facts (quiescent
  currents, register defaults, thresholds, protocol responses) that the
  generated conformance tests must verify against the simulator. A model
  without passing claims never installs.
- **Trust model**: generated code runs only inside its conformance pytest
  during generation, and installs to `twin_models/` for human review — it is
  never auto-registered into the library. Scenarios opt in per board with
  `board.model_dirs: [twin_models]`. Treat a generated model like a PR from
  a new contributor: read it, run its tests, then trust it.
- **Provenance**: the spec JSON ships next to the model; `confidence_notes`
  records where the agent estimated instead of extracted.
- **Credentials**: `ant auth login` or `ANTHROPIC_API_KEY`;
  `pip install -e ".[factory]"` pulls the SDK. Without credentials the rest
  of embedded-twin is unaffected.

Still ahead for v0.3 proper (ROADMAP): an eval suite of datasheet→model
tasks with graded fidelity, confidence grading surfaced in bind reports, and
a shared community library of reviewed generated models.
