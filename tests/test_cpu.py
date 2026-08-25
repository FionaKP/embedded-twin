import pytest

from twin.build import build_twin
from twin.core.kernel import MS, SEC, US
from twin.cpu import asmtool
from twin.ingest import parse_kicad_netlist, merge_bom, bind_models
from twin.ir import BoardIR, ComponentIR, NetIR, NetNode

from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

needs_clang = pytest.mark.skipif(not asmtool.clang_available(),
                                 reason="clang not available")

BLINK_ASM = """
.thumb_func
reset:
    ldr r0, =0x40000000     @ GPIO base
    movs r1, #0x20          @ bit 5 = PA5
    str r1, [r0, #8]        @ DIR: PA5 output
    movs r2, #5             @ 5 blinks
loop:
    str r1, [r0, #0x0C]     @ SET
    bl delay_1ms
    str r1, [r0, #0x10]     @ CLR
    bl delay_1ms
    subs r2, #1
    bne loop
    ldr r0, =0x40003008     @ DBG
    movs r1, #'D'
    str r1, [r0]
    movs r1, #'N'
    str r1, [r0]
    movs r1, #10
    str r1, [r0]
    ldr r0, =0x40003004     @ EXIT
    movs r1, #1
    str r1, [r0]
1:  b 1b

.thumb_func
delay_1ms:
    ldr r3, =0x40003000     @ SLEEP_US
    ldr r4, =1000
    str r4, [r3]
    bx lr
.ltorg
"""


def blinky_board():
    board = parse_kicad_netlist(FIXTURES / "blinky.net")
    merge_bom(board, FIXTURES / "blinky_bom.csv")
    bind_models(board)
    return board


@needs_clang
def test_unicorn_firmware_blinks_real_led():
    image = asmtool.make_image(BLINK_ASM)
    board = blinky_board()
    board.components["U1"].params.update({
        "pinmap": {"PA5": 5, "PA0": 0}, "slice_us": 100})
    twin = build_twin(board, external_supplies={"VBAT": 3.7})
    twin.comp("U1").params["firmware"] = image  # loaded at start()
    twin.start()
    twin.kernel.run_until(50 * MS)

    led_edges = twin.kernel.trace.transitions("/LED_CTRL")
    highs = [t for t, lvl in led_edges if lvl == "1"]
    lows = [t for t, lvl in led_edges if lvl == "0"]
    assert len(highs) == 5
    assert len(lows) == 6  # DIR-output-enable low at t=0, then 5 blink-offs
    # blink period ~1 ms per phase
    assert 900 * US <= (lows[1] - highs[0]) <= 1100 * US
    # LED component actually lit
    states = [e.value for e in twin.kernel.trace.select(kind="state", name="LED1")]
    assert states.count("on") == 5

    # debug print and clean halt
    assert any(v == "DN" for _t, _n, v in twin.kernel.trace.logs())
    assert twin.comp("U1").backend.halted


@needs_clang
def test_firmware_reads_gpio_input():
    # wait for button press (PA0 low), then set PA5 high and exit
    asm = """
.thumb_func
reset:
    ldr r0, =0x40000000
    movs r1, #0x20
    str r1, [r0, #8]        @ PA5 output
wait:
    ldr r3, =0x40003000
    movs r4, #100
    str r4, [r3]            @ sleep 100 us (poll politely)
    ldr r2, [r0, #4]        @ GPIO_IN
    movs r5, #1
    tst r2, r5              @ PA0 still high?
    bne wait
    str r1, [r0, #0x0C]     @ press seen: PA5 <- 1
    ldr r0, =0x40003004
    str r1, [r0]            @ EXIT
1:  b 1b
.ltorg
"""
    image = asmtool.make_image(asm)
    board = blinky_board()
    board.components["U1"].params.update({
        "pinmap": {"PA5": 5, "PA0": 0}, "slice_us": 100, "firmware": None})
    twin = build_twin(board, external_supplies={"VBAT": 3.7})
    twin.comp("U1").params["firmware"] = image
    twin.start()
    twin.kernel.schedule_at(5 * MS, twin.comp("SW1").press)
    twin.kernel.run_until(20 * MS)
    assert twin.comp("LED1").lit
    t_on = twin.kernel.trace.transitions("/LED_CTRL")[-1][0]
    assert 5 * MS <= t_on <= 6 * MS  # reacted within polling latency


@needs_clang
def test_uart_tx_from_firmware():
    asm = """
.thumb_func
reset:
    ldr r0, =0x40001000     @ UART0
    movs r1, #'H'
    str r1, [r0]
    movs r1, #'I'
    str r1, [r0]
    ldr r0, =0x40003004
    movs r1, #1
    str r1, [r0]
1:  b 1b
.ltorg
"""
    b = BoardIR(name="uartboard")
    b.add_component(ComponentIR(ref="U1", model="mcu.cortex_m",
                                params={"uarts": [{"tx": "TX", "rx": None}],
                                        "pinmap": {"TX": 9}, "slice_us": 100},
                                pins={"1": "TX", "2": "VDD"}))
    b.add_net(NetIR("MCU_TX", "signal", [NetNode("U1", "1")]))
    b.add_net(NetIR("+3V3", "power", [NetNode("U1", "2")]))
    twin = build_twin(b, external_supplies={"+3V3": 3.3})
    twin.comp("U1").params["firmware"] = asmtool.make_image(asm)
    twin.start()
    twin.kernel.run_until(5 * MS)
    assert twin.kernel.trace.uart_bytes("MCU_TX") == b"HI"


def test_behavioral_firmware_and_power_states():
    board = blinky_board()
    board.components["U1"].params["pinmap"] = {"PA5": 5, "PA0": 0}
    twin = build_twin(board, external_supplies={"VBAT": 3.7})

    def fw(api):
        api.gpio_write("PA5", 0)
        state = {"on": False}

        def blink():
            state["on"] = not state["on"]
            api.gpio_write("PA5", 1 if state["on"] else 0)
        api.every(100_000, blink)  # 100 ms

    twin.comp("U1").load_behavioral(fw)
    twin.start()
    twin.kernel.run_until(1 * SEC)
    edges = twin.kernel.trace.transitions("/LED_CTRL")
    assert 9 <= len(edges) <= 11

    # MCU run current is accounted on +3V3 and reflected onto VBAT
    report = twin.power.report()
    assert report["rails"]["+3V3"]["avg_ma"] > 1
    assert report["rails"]["VBAT"]["avg_ma"] > report["rails"]["+3V3"]["avg_ma"] - 0.1


@needs_clang
def test_firmware_fault_is_contained_and_reported():
    asm = """
.thumb_func
reset:
    ldr r0, =0x90000000     @ unmapped address
    ldr r1, [r0]
1:  b 1b
.ltorg
"""
    b = BoardIR(name="faulty")
    b.add_component(ComponentIR(ref="U1", model="mcu.cortex_m",
                                params={"slice_us": 100}, pins={"1": "VDD"}))
    b.add_net(NetIR("+3V3", "power", [NetNode("U1", "1")]))
    twin = build_twin(b, external_supplies={"+3V3": 3.3})
    twin.comp("U1").params["firmware"] = asmtool.make_image(asm)
    twin.start()
    twin.kernel.run_until(1 * MS)
    assert twin.comp("U1").state == "faulted"
    assert any("FAULT" in str(v) for _t, _n, v in twin.kernel.trace.logs())
