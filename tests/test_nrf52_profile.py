"""nRF52 vendor-map tests: task/event peripherals, EasyDMA UARTE, GPIO P0."""
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
#define CLOCK_HFCLKSTART   REG(0x40000000)
#define CLOCK_HFCLKSTARTED REG(0x40000100)
#define UARTE_STARTRX      REG(0x40002000)
#define UARTE_STARTTX      REG(0x40002008)
#define UARTE_ENDRX        REG(0x40002110)
#define UARTE_ENDTX        REG(0x40002120)
#define UARTE_INTENSET     REG(0x40002304)
#define UARTE_ENABLE       REG(0x40002500)
#define UARTE_RXD_PTR      REG(0x40002534)
#define UARTE_RXD_MAXCNT   REG(0x40002538)
#define UARTE_TXD_PTR      REG(0x40002544)
#define UARTE_TXD_MAXCNT   REG(0x40002548)
#define GPIO_OUTSET        REG(0x50000508)
#define GPIO_OUTCLR        REG(0x5000050C)
#define GPIO_DIRSET        REG(0x50000518)
#define SYST_CSR           REG(0xE000E010)
#define SYST_RVR           REG(0xE000E014)
#define NVIC_ISER0         REG(0xE000E100)
extern uint32_t _estack;
void Reset_Handler(void);
void SysTick_Handler(void);
void UARTE0_IRQHandler(void);
__attribute__((section(".vectors")))
const void* vectors[64] = {
    [0] = &_estack, [1] = Reset_Handler,
    [15] = SysTick_Handler, [16 + 2] = UARTE0_IRQHandler,
};
"""


def nrf_board() -> BoardIR:
    b = BoardIR(name="nrfboard")
    b.add_component(ComponentIR(
        ref="U1", model="mcu.cortex_m",
        params={"profile": "nrf52", "clock_hz": 64_000_000, "slice_us": 1000,
                "uarts": [{"periph": "UARTE0", "tx": "P0.6", "rx": "P0.8",
                           "baud": 115200}]},
        pins={"1": "P0.13", "2": "P0.6", "3": "P0.8", "4": "VDD"}))
    b.add_net(NetIR("LED", "signal", [NetNode("U1", "1")]))
    b.add_net(NetIR("MCU_TX", "signal", [NetNode("U1", "2")]))
    b.add_net(NetIR("HOST_TX", "signal", [NetNode("U1", "3")]))
    b.add_net(NetIR("+3V3", "power", [NetNode("U1", "4")]))
    return b


def run_nrf(code: str, run_s: float = 1.0, host_send: bytes | None = None):
    image = cbuild.compile_c(HEADER + code, profile="nrf52")
    twin = build_twin(nrf_board(), external_supplies={"+3V3": 3.3})
    twin.comp("U1").params["firmware"] = image
    twin.start()
    host = Uart(twin.kernel, "host", tx_net=twin.net("HOST_TX"),
                rx_net=twin.net("MCU_TX"), baud=115200)
    got = []
    host.on_byte(got.append)
    if host_send:
        twin.kernel.schedule_at(10 * MS, host.send, host_send)
    twin.kernel.run_until(int(run_s * SEC))
    return twin, bytes(got)


@needs_zig
def test_nrf52_systick_blink_via_outset_outclr():
    twin, _ = run_nrf(r"""
volatile uint32_t tick;
void SysTick_Handler(void) { tick++; }
void UARTE0_IRQHandler(void) {}
void Reset_Handler(void) {
    CLOCK_HFCLKSTART = 1;
    while (!CLOCK_HFCLKSTARTED) {}
    GPIO_DIRSET = (1u << 13);
    SYST_RVR = 64000 - 1;                    // 1 ms @ 64 MHz
    SYST_CSR = 7;
    uint32_t last = 0, on = 0;
    for (;;) {
        __asm volatile("wfi");
        if (tick - last >= 100) {
            last = tick;
            if ((on ^= 1)) GPIO_OUTSET = (1u << 13);
            else GPIO_OUTCLR = (1u << 13);
        }
    }
}
""", run_s=1.0)
    edges = twin.kernel.trace.transitions("LED")
    assert 8 <= len(edges) <= 11   # 100 ms toggles over 1 s
    deltas = [b - a for (a, _), (b, _) in zip(edges, edges[1:])]
    assert all(90 * MS <= d <= 112 * MS for d in deltas)


@needs_zig
def test_nrf52_uarte_easydma_tx():
    twin, got = run_nrf(r"""
void SysTick_Handler(void) {}
void UARTE0_IRQHandler(void) {}
static volatile uint8_t buf[8];
void Reset_Handler(void) {
    UARTE_ENABLE = 8;
    buf[0]='n'; buf[1]='R'; buf[2]='F'; buf[3]='!';
    UARTE_TXD_PTR = (uint32_t)buf;
    UARTE_TXD_MAXCNT = 4;
    UARTE_STARTTX = 1;
    while (!UARTE_ENDTX) {}
    for (;;) __asm volatile("wfi");
}
""", run_s=0.5)
    assert got == b"nRF!"


@needs_zig
def test_nrf52_uarte_rx_interrupt_echo():
    twin, got = run_nrf(r"""
void SysTick_Handler(void) {}
static volatile uint8_t rx[2];
static volatile uint8_t tx[2];
void UARTE0_IRQHandler(void) {
    if (UARTE_ENDRX) {
        UARTE_ENDRX = 0;
        tx[0] = rx[0] + 1; tx[1] = rx[1] + 1;
        UARTE_TXD_PTR = (uint32_t)tx;
        UARTE_TXD_MAXCNT = 2;
        UARTE_STARTTX = 1;
        UARTE_RXD_PTR = (uint32_t)rx;       // re-arm
        UARTE_RXD_MAXCNT = 2;
        UARTE_STARTRX = 1;
    }
}
void Reset_Handler(void) {
    UARTE_ENABLE = 8;
    UARTE_INTENSET = (1u << 4);             // ENDRX
    NVIC_ISER0 = (1u << 2);                 // UARTE0 IRQ
    UARTE_RXD_PTR = (uint32_t)rx;
    UARTE_RXD_MAXCNT = 2;
    UARTE_STARTRX = 1;
    for (;;) __asm volatile("wfi");
}
""", run_s=0.5, host_send=b"AB")
    assert got == b"BC"
    assert twin.comp("U1").state != "faulted"
