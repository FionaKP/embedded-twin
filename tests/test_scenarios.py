"""End-to-end: the asset-tracker ringer scenarios must pass."""
from pathlib import Path

import pytest

from twin.scenario import run_scenario, to_markdown

EXAMPLES = Path(__file__).parent.parent / "examples"
SCEN = EXAMPLES / "asset-tracker" / "scenarios"


@pytest.mark.parametrize("name", ["smoke_test", "urban_canyon_recovery",
                                  "network_congestion"])
def test_scenario_passes(name):
    result = run_scenario(str(SCEN / f"{name}.yaml"))
    failed = [r for r in result["assertions"] if not r["passed"]]
    assert result["passed"], f"{name}: {[r['evidence'] for r in failed]}"


def test_battery_scenario_and_report_render():
    result = run_scenario(str(SCEN / "battery_life_24h.yaml"))
    assert result["passed"]
    # physics sanity: a 2000 mAh cell must not be magically full or empty
    soc = result["power"]["batteries"][0]["soc"]
    assert 0.8 < soc < 0.95
    md = to_markdown(result)
    assert "Verdict: PASS" in md and "lock" in md


def test_stm32_c_firmware_scenario():
    from twin.cpu import cbuild
    if not cbuild.zig_available():
        pytest.skip("ziglang not installed")
    result = run_scenario(str(EXAMPLES / "stm32-button-led" / "scenarios"
                              / "button_blink.yaml"))
    failed = [r["evidence"] for r in result["assertions"] if not r["passed"]]
    assert result["passed"], failed


def test_determinism_across_runs():
    a = run_scenario(str(SCEN / "smoke_test.yaml"))
    b = run_scenario(str(SCEN / "smoke_test.yaml"))
    assert a["power"] == b["power"]
    assert [r["evidence"] for r in a["assertions"]] == \
           [r["evidence"] for r in b["assertions"]]
