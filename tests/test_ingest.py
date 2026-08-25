from pathlib import Path

from twin.ingest import parse_kicad_netlist, merge_bom, bind_models
from twin.ir import BoardIR

FIXTURES = Path(__file__).parent / "fixtures"


def load_blinky() -> BoardIR:
    board = parse_kicad_netlist(FIXTURES / "blinky.net")
    merge_bom(board, FIXTURES / "blinky_bom.csv")
    return board


def test_netlist_structure():
    board = load_blinky()
    assert set(board.components) == {"U1", "U2", "R1", "R2", "LED1", "SW1"}
    assert board.components["U2"].pins["5"] == "VOUT"
    assert board.nets["+3V3"].net_class == "power"
    assert board.nets["GND"].net_class == "ground"
    assert board.nets["/LED_CTRL"].net_class == "signal"
    assert board.nets_of("R1") == {"1": "/LED_CTRL", "2": "/LED_A"}


def test_bom_merge_params_and_multi_ref_rows():
    board = load_blinky()
    assert board.components["U2"].part_number == "AP2112K-3.3TRG1"
    assert board.components["U2"].params == {"vout": 3.3, "i_limit_ma": 600}


def test_model_binding():
    board = load_blinky()
    report = bind_models(board)
    assert board.components["U2"].model == "regulator.ldo"      # by MPN
    assert board.components["U1"].model == "mcu.cortex_m"       # by MPN
    assert board.components["R1"].model == "passive.resistor"   # by refdes
    assert board.components["LED1"].model == "passive.led"
    assert board.components["SW1"].model == "input.button"
    assert report["unbound"] == []


def test_ir_json_roundtrip_and_stable_hash():
    board = load_blinky()
    bind_models(board)
    clone = BoardIR.from_json(board.to_json())
    assert clone.to_json() == board.to_json()
    assert clone.sha256() == board.sha256()
