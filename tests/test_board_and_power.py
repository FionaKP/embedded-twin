from pathlib import Path

from twin.build import build_twin
from twin.core import SimKernel, Net, Drive
from twin.core.kernel import MS, SEC
from twin.comm import Uart, I2CBus
from twin.ingest import parse_kicad_netlist, merge_bom, bind_models
from twin.ir import BoardIR, ComponentIR, NetIR, NetNode

FIXTURES = Path(__file__).parent / "fixtures"


def blinky_twin():
    board = parse_kicad_netlist(FIXTURES / "blinky.net")
    merge_bom(board, FIXTURES / "blinky_bom.csv")
    bind_models(board)
    board.components["U1"].model = ""  # MCU comes in the CPU phase
    twin = build_twin(board, external_supplies={"VBAT": 3.7})
    twin.start()
    return twin


def test_ldo_regulates_from_external_supply():
    twin = blinky_twin()
    rail = twin.net("+3V3")
    assert rail.is_high and rail.voltage == 3.3
    assert twin.comp("U2").state == "regulating"


def test_button_with_pullup():
    twin = blinky_twin()
    btn_net = twin.net("/BTN")
    assert btn_net.is_high  # R2 pull-up from +3V3
    twin.comp("SW1").press()
    twin.kernel.run()
    assert btn_net.is_low
    twin.comp("SW1").release()
    twin.kernel.run()
    assert btn_net.is_high


def test_led_through_series_resistor():
    twin = blinky_twin()
    led = twin.comp("LED1")
    assert not led.lit
    # stand in for the MCU pin until the CPU phase
    twin.net("/LED_CTRL").drive("test", Drive.high(3.3))
    twin.kernel.run()
    assert led.lit
    twin.net("/LED_CTRL").drive("test", Drive.low())
    twin.kernel.run()
    assert not led.lit


def synth_battery_board() -> BoardIR:
    """Battery -> LDO -> load, minimal IR built by hand."""
    b = BoardIR(name="synth")
    b.add_component(ComponentIR(ref="BT1", model="power.battery",
                                params={"capacity_mah": 100, "r_internal": 0.05}))
    b.add_component(ComponentIR(ref="U1", model="regulator.ldo",
                                params={"vout": 3.3, "iq_ua": 50},
                                pins={"1": "VIN", "2": "GND", "5": "VOUT"}))
    b.add_net(NetIR("VBAT", "power", [NetNode("BT1", "1"), NetNode("U1", "1")]))
    b.add_net(NetIR("+3V3", "power", [NetNode("U1", "5")]))
    b.add_net(NetIR("GND", "ground", [NetNode("U1", "2")]))
    return b


def test_power_accounting_battery_ldo_load():
    twin = build_twin(synth_battery_board())
    twin.start()
    # a steady 10 mA load on the 3.3 V rail for 1 hour of sim time
    twin.power.set_load("LOAD", "+3V3", 0.010)
    twin.kernel.run_until(3600 * SEC)
    report = twin.power.report()
    assert abs(report["rails"]["+3V3"]["avg_ma"] - 10) < 0.1
    assert abs(report["rails"]["+3V3"]["charge_mah"] - 10) < 0.1
    # LDO reflects load + Iq onto VBAT
    assert abs(report["rails"]["VBAT"]["charge_mah"] - 10.05) < 0.1
    bat = report["batteries"][0]
    assert 0.88 < bat["soc"] < 0.92  # ~10 mAh out of 100 mAh
    assert bat["voltage"] < 4.2


def test_ldo_thermal_rises_under_load():
    twin = build_twin(synth_battery_board())
    twin.start()
    twin.power.set_load("LOAD", "+3V3", 0.150)  # 150 mA: LDO burns (4.2-3.3)*0.15 W
    twin.kernel.run_until(600 * SEC)
    report = twin.power.report()
    # dissipation ~0.13 W * 250 C/W ~= +33C over ambient at steady state
    assert report["thermal"]["U1"] > 45


def test_uart_link_timing_and_trace():
    k = SimKernel()
    tx = Net(k, "MCU_TX")
    a = Uart(k, "mcu", tx_net=tx, rx_net=None, baud=115200)
    got = []
    b = Uart(k, "modem", tx_net=None, rx_net=tx, baud=115200)
    b.on_byte(got.append)
    a.send(b"AT\r\n")
    k.run()
    assert bytes(got) == b"AT\r\n"
    assert k.trace.uart_bytes("MCU_TX") == b"AT\r\n"
    # 4 bytes * 10 bits / 115200 baud ~= 347 us
    assert 300_000 < k.now < 400_000


def test_i2c_temp_sensor():
    b = BoardIR(name="i2c")
    b.add_component(ComponentIR(ref="U3", model="sensor.temp_i2c",
                                params={"addr": 0x48},
                                pins={"1": "SCL", "2": "SDA", "3": "VDD"}))
    b.add_net(NetIR("SCL", "signal", [NetNode("U3", "1")]))
    b.add_net(NetIR("SDA", "signal", [NetNode("U3", "2")]))
    b.add_net(NetIR("+3V3", "power", [NetNode("U3", "3")]))
    twin = build_twin(b, external_supplies={"+3V3": 3.3})
    twin.start()
    sensor = twin.comp("U3")
    sensor.set_temperature(25.5)
    bus = I2CBus.on_nets(twin.kernel, twin.net("SCL"), twin.net("SDA"))
    assert bus.write(0x48, b"\x00")
    data = bus.read(0x48, 2)
    raw = int.from_bytes(data, "big", signed=True) >> 4
    assert abs(raw / 16 - 25.5) < 0.1
    assert bus.write(0x49, b"\x00") is False  # nobody home -> NACK


def test_unbound_components_warn_not_crash():
    board = parse_kicad_netlist(FIXTURES / "blinky.net")
    twin = build_twin(board)  # no binding at all
    assert any("unbound" in w for w in twin.warnings)
