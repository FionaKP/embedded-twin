"""Eagle ingest + the real Adafruit Feather F405 board end to end."""
from pathlib import Path

import pytest

from twin.cpu import cbuild
from twin.ingest import parse_eagle_sch, bind_models
from twin.scenario import run_scenario

EX = Path(__file__).parent.parent / "examples" / "feather-f405"
SCH = EX / "hardware" / "feather_f405.sch"


def test_eagle_ingest_recovers_the_real_board():
    b = parse_eagle_sch(SCH)
    assert len(b.components) == 60
    assert len(b.nets) == 59
    # the power chain, exactly as routed
    assert b.nets_of("U2") == {"GND": "GND", "OUT": "+3V3", "EN": "EN",
                               "IN": "VHI"}
    assert b.nets_of("Q3") == {"G": "VBUS", "D": "VBAT", "S": "VHI"}
    # supply symbols dropped, their nets classified
    assert b.nets["GND"].net_class == "ground"
    assert b.nets["+3V3"].net_class == "power"
    assert b.nets["VBUS"].net_class == "power"
    # MCU pin decorated names preserved
    mcu_nets = b.nets_of("U$3")
    assert mcu_nets["PC1/ADC123_IN11(5T)"] == "D13"


def test_value_based_binding_on_eagle_parts():
    b = parse_eagle_sch(SCH)
    report = bind_models(b)
    assert b.components["U$3"].model == "mcu.cortex_m"     # STM32F405R value
    assert b.components["U2"].model == "regulator.ldo"     # AP2112-3.3
    assert b.components["U1"].model == "memory.spi_flash"  # 25Q16
    assert b.components["LED1"].model == "led.ws2812"      # WS2812B
    assert b.components["D4"].model == "passive.diode"
    assert "U3" in report["unbound"]                       # charger: factory's job


@pytest.mark.skipif(not cbuild.zig_available(), reason="ziglang not installed")
def test_feather_usb_selftest_scenario():
    result = run_scenario(str(EX / "scenarios" / "usb_selftest.yaml"))
    failed = [r["evidence"] for r in result["assertions"] if not r["passed"]]
    assert result["passed"], failed


@pytest.mark.skipif(not cbuild.zig_available(), reason="ziglang not installed")
def test_feather_battery_scenario():
    result = run_scenario(str(EX / "scenarios" / "battery_only.yaml"))
    failed = [r["evidence"] for r in result["assertions"] if not r["passed"]]
    assert result["passed"], failed
    # USB rail must carry nothing with the cable unplugged
    assert result["power"]["rails"]["VBUS"]["avg_ma"] == 0.0
    assert result["power"]["batteries"][0]["soc"] > 0.95
