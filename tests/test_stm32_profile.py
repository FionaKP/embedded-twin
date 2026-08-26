"""Vendor-map tests: real C firmware, compiled with zig, on STM32F4 addresses."""
import pytest

from twin.build import build_twin
from twin.comm import Uart
from twin.core.kernel import MS, SEC
from twin.cpu import cbuild
from twin.ir import BoardIR, ComponentIR, NetIR, NetNode

needs_zig = pytest.mark.skipif(not cbuild.zig_available(),
                               reason="ziglang not installed")

HEADER = r"""
#include <stdint.h>
#define REG(a) (*(volatile uint32_t*)(a))
#define RCC_CR        REG(0x40023800)
#define RCC_CFGR      REG(0x40023808)
#define RCC_AHB1ENR   REG(0x40023830)
#define RCC_APB1ENR   REG(0x40023840)
#define GPIOA_MODER   REG(0x40020000)
#define GPIOA_IDR     REG(0x40020010)
#define GPIOA_ODR     REG(0x40020014)
#define GPIOA_BSRR    REG(0x40020018)
#define USART2_SR     REG(0x40004400)
#define USART2_DR     REG(0x40004404)
#define USART2_CR1    REG(0x4000440C)
#define SYST_CSR      REG(0xE000E010)
#define SYST_RVR      REG(0xE000E014)
#define SYST_CVR      REG(0xE000E018)
#define NVIC_ISER1    REG(0xE000E104)
extern uint32_t _estack;
void Reset_Handler(void);
void SysTick_Handler(void);
void USART2_IRQHandler(void);
__attribute__((section(".vectors")))
const void* vectors[64] = {
    [0] = &_estack, [1] = Reset_Handler,
    [15] = SysTick_Handler, [16 + 38] = USART2_IRQHandler,
};
"""


def mcu_board(uarts=None) -> BoardIR:
    b = BoardIR(name="stm32board")
    params = {"profile": "stm32f4", "clock_hz": 16_000_000, "slice_us": 1000}
    if uarts:
        params["uarts"] = uarts
    b.add_component(ComponentIR(
        ref="U1", model="mcu.cortex_m", params=params,
        pins={"1": "PA5", "2": "PA0", "3": "PA2", "4": "PA3", "5": "VDD"}))
    b.add_net(NetIR("LED", "signal", [NetNode("U1", "1")]))
    b.add_net(NetIR("BTN", "signal", [NetNode("U1", "2")]))
    b.add_net(NetIR("MCU_TX", "signal", [NetNode("U1", "3")]))
    b.add_net(NetIR("HOST_TX", "signal", [NetNode("U1", "4")]))
    b.add_net(NetIR("+3V3", "power", [NetNode("U1", "5")]))
    return b


def run_fw(code: str, board=None, run_s: float = 1.0):
    image = cbuild.compile_c(HEADER + code, profile="stm32f4")
    b = board or mcu_board()
    twin = build_twin(b, external_supplies={"+3V3": 3.3})
    twin.comp("U1").params["firmware"] = image
    twin.start()
    twin.kernel.run_until(int(run_s * SEC))
    return twin


@needs_zig
def test_systick_interrupt_blink_with_wfi_sleep():
    twin = run_fw(r"""
volatile uint32_t tick;
void SysTick_Handler(void) { tick++; }
void USART2_IRQHandler(void) {}
void Reset_Handler(void) {
    RCC_AHB1ENR |= 1u;                                  // GPIOA clock
    GPIOA_MODER = (GPIOA_MODER & ~(3u << 10)) | (1u << 10);  // PA5 output
    SYST_RVR = 16000 - 1;                               // 1 ms @ 16 MHz
    SYST_CVR = 0;
    SYST_CSR = 7;                                       // enable+tickint+core clk
    uint32_t last = 0;
    for (;;) {
        __asm volatile("wfi");
        if (tick - last >= 50) { last = tick; GPIOA_ODR ^= (1u << 5); }
    }
}
""", run_s=2.0)
    edges = twin.kernel.trace.transitions("LED")
    # toggle every 50 ms -> ~40 edges in 2 s (allow catch-up jitter)
    assert 36 <= len(edges) <= 42, f"got {len(edges)} edges"
    # period check between consecutive toggles
    deltas = [b - a for (a, _), (b, _) in zip(edges, edges[1:])]
    assert all(45 * MS <= d <= 56 * MS for d in deltas)
    # WFI put the MCU to sleep between ticks
    power_states = [e.value for e in
                    twin.kernel.trace.select(kind="state", name="U1.power")]
    assert "sleep" in power_states


@needs_zig
def test_hal_style_clock_init_sequence_completes():
    twin = run_fw(r"""
void SysTick_Handler(void) {}
void USART2_IRQHandler(void) {}
void Reset_Handler(void) {
    RCC_CR |= (1u << 16);                       // HSEON
    while (!(RCC_CR & (1u << 17))) {}           // wait HSERDY
    RCC_CR |= (1u << 24);                       // PLLON
    while (!(RCC_CR & (1u << 25))) {}           // wait PLLRDY
    RCC_CFGR = (RCC_CFGR & ~3u) | 2u;           // SW = PLL
    while (((RCC_CFGR >> 2) & 3u) != 2u) {}     // wait SWS
    RCC_AHB1ENR |= 1u;
    GPIOA_MODER |= (1u << 10);
    GPIOA_BSRR = (1u << 5);                     // success: PA5 high
    for (;;) __asm volatile("wfi");
}
""", run_s=0.1)
    assert twin.net("LED").is_high
    assert twin.comp("U1").state != "faulted"


@needs_zig
def test_usart2_rx_interrupt_echo():
    board = mcu_board(uarts=[{"periph": "USART2", "tx": "PA2", "rx": "PA3",
                              "baud": 115200}])
    image = cbuild.compile_c(HEADER + r"""
void SysTick_Handler(void) {}
void USART2_IRQHandler(void) {
    if (USART2_SR & (1u << 5)) {
        uint32_t b = USART2_DR;
        USART2_DR = b + 1;                      // echo transformed
    }
}
void Reset_Handler(void) {
    RCC_APB1ENR |= (1u << 17);                  // USART2 clock
    USART2_CR1 = (1u << 13) | (1u << 5) | (1u << 3) | (1u << 2);
    NVIC_ISER1 = 1u << (38 - 32);               // enable USART2 IRQ
    for (;;) __asm volatile("wfi");
}
""", profile="stm32f4")
    twin = build_twin(board, external_supplies={"+3V3": 3.3})
    twin.comp("U1").params["firmware"] = image
    twin.start()
    host = Uart(twin.kernel, "host", tx_net=twin.net("HOST_TX"),
                rx_net=twin.net("MCU_TX"), baud=115200)
    got = []
    host.on_byte(got.append)
    twin.kernel.schedule_at(10 * MS, host.send, b"AB")
    twin.kernel.run_until(100 * MS)
    assert bytes(got) == b"BC"


@needs_zig
def test_gpio_input_polling():
    twin_board = mcu_board()
    image = cbuild.compile_c(HEADER + r"""
volatile uint32_t tick;
void SysTick_Handler(void) { tick++; }
void USART2_IRQHandler(void) {}
void Reset_Handler(void) {
    RCC_AHB1ENR |= 1u;
    GPIOA_MODER = (GPIOA_MODER & ~(3u << 10)) | (1u << 10);  // PA5 out, PA0 in
    SYST_RVR = 16000 - 1; SYST_CSR = 7;
    for (;;) {
        __asm volatile("wfi");
        if (!(GPIOA_IDR & 1u)) GPIOA_BSRR = (1u << 5);       // PA0 low -> LED
    }
}
""", profile="stm32f4")
    twin = build_twin(twin_board, external_supplies={"+3V3": 3.3})
    twin.comp("U1").params["firmware"] = image
    twin.start()
    from twin.core import Drive
    twin.net("BTN").drive("pullup", Drive.pull_up(3.3))
    twin.kernel.schedule_at(50 * MS, twin.net("BTN").drive, "press", Drive.low())
    twin.kernel.run_until(200 * MS)
    edges = twin.kernel.trace.transitions("LED")
    assert edges and edges[-1][1] == "1"
    assert edges[-1][0] >= 50 * MS
