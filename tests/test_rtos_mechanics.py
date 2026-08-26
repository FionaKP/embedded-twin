"""The FreeRTOS unlock, proven with a minimal RTOS written the way the
FreeRTOS ARM_CM3 port is written: SVC starts the first task, SysTick pends
PendSV, PendSV context-switches between tasks running on PSP stacks, and
BASEPRI masks the scheduler for critical sections."""
import pytest

from twin.build import build_twin
from twin.core.kernel import MS, SEC
from twin.cpu import cbuild
from twin.ir import BoardIR, ComponentIR, NetIR, NetNode

needs_zig = pytest.mark.skipif(not cbuild.zig_available(),
                               reason="ziglang not installed")

MINI_RTOS = r"""
#include <stdint.h>
#define REG(a) (*(volatile uint32_t*)(a))
#define RCC_AHB1ENR   REG(0x40023830)
#define GPIOA_MODER   REG(0x40020000)
#define GPIOA_ODR     REG(0x40020014)
#define SYST_CSR      REG(0xE000E010)
#define SYST_RVR      REG(0xE000E014)
#define SCB_ICSR      REG(0xE000ED04)
#define SCB_SHPR3     REG(0xE000ED20)

extern uint32_t _estack;
void Reset_Handler(void);
void SVC_Handler(void);
void PendSV_Handler(void);
void SysTick_Handler(void);

__attribute__((section(".vectors")))
const void* vectors[64] = {
    [0] = &_estack, [1] = Reset_Handler,
    [11] = SVC_Handler, [14] = PendSV_Handler, [15] = SysTick_Handler,
};

uint32_t *task_sp[2];
uint32_t cur_task;
static uint32_t stacks[2][256];
static volatile uint32_t g_tick;

void SysTick_Handler(void) {
    g_tick++;
    SCB_ICSR = (1u << 28);              /* pend PendSV: request switch */
}

/* FreeRTOS-style first-task start: pop r4-r11 from the task stack,
   point PSP at its exception frame, return to thread mode on PSP. */
__attribute__((naked)) void SVC_Handler(void) {
    __asm volatile(
        "movw r1, #:lower16:task_sp\n"
        "movt r1, #:upper16:task_sp\n"
        "movw r2, #:lower16:cur_task\n"
        "movt r2, #:upper16:cur_task\n"
        "ldr r3, [r2]\n"
        "ldr r0, [r1, r3, lsl #2]\n"
        "ldmia r0!, {r4-r11}\n"
        "msr psp, r0\n"
        "movs r0, #0\n"
        "msr basepri, r0\n"
        "orr lr, lr, #0x0d\n"
        "bx lr\n");
}

/* FreeRTOS-style context switch on PendSV. */
__attribute__((naked)) void PendSV_Handler(void) {
    __asm volatile(
        "mrs r0, psp\n"
        "stmdb r0!, {r4-r11}\n"
        "movw r1, #:lower16:task_sp\n"
        "movt r1, #:upper16:task_sp\n"
        "movw r2, #:lower16:cur_task\n"
        "movt r2, #:upper16:cur_task\n"
        "ldr r3, [r2]\n"
        "str r0, [r1, r3, lsl #2]\n"
        "eor r3, r3, #1\n"
        "str r3, [r2]\n"
        "ldr r0, [r1, r3, lsl #2]\n"
        "ldmia r0!, {r4-r11}\n"
        "msr psp, r0\n"
        "bx lr\n");
}

static void task_body(uint32_t pin_bit, int critical_at) {
    uint32_t last = 0, n = 0;
    for (;;) {
        while (g_tick == last) {}
        last = g_tick;
        GPIOA_ODR ^= pin_bit;
        n++;
        if (critical_at && n == (uint32_t)critical_at) {
            uint32_t b = 0x50;          /* mask SysTick (0xE0) + PendSV (0xF0) */
            __asm volatile("msr basepri, %0" :: "r"(b));
            for (volatile uint32_t i = 0; i < 300000; i++) {}
            b = 0;
            __asm volatile("msr basepri, %0" :: "r"(b));
        }
    }
}

static void task0(void) { task_body(1u << 5, 30); }
static void task1(void) { task_body(1u << 6, 0); }

static uint32_t *init_stack(uint32_t *top, void (*entry)(void)) {
    top -= 8;                            /* hardware frame */
    top[7] = 0x01000000;                 /* xPSR: thumb */
    top[6] = (uint32_t)entry;            /* PC */
    top[5] = 0xFFFFFFFF;                 /* LR: tasks never return */
    top -= 8;                            /* r4-r11 slot */
    return top;
}

void Reset_Handler(void) {
    RCC_AHB1ENR |= 1u;
    GPIOA_MODER = (GPIOA_MODER & ~((3u << 10) | (3u << 12)))
                | (1u << 10) | (1u << 12);          /* PA5, PA6 outputs */
    task_sp[0] = init_stack(&stacks[0][256], task0);
    task_sp[1] = init_stack(&stacks[1][256], task1);
    SCB_SHPR3 = (0xE0u << 24) | (0xF0u << 16);      /* SysTick 0xE0, PendSV 0xF0 */
    SYST_RVR = 16000 - 1;                            /* 1 ms tick @ 16 MHz */
    SYST_CSR = 7;
    __asm volatile("svc #0");                        /* start the scheduler */
    for (;;) {}                                      /* never reached */
}
"""


def rtos_board() -> BoardIR:
    b = BoardIR(name="rtosboard")
    b.add_component(ComponentIR(
        ref="U1", model="mcu.cortex_m",
        params={"profile": "stm32f4", "clock_hz": 16_000_000, "slice_us": 1000},
        pins={"1": "PA5", "2": "PA6", "3": "VDD"}))
    b.add_net(NetIR("T0_PIN", "signal", [NetNode("U1", "1")]))
    b.add_net(NetIR("T1_PIN", "signal", [NetNode("U1", "2")]))
    b.add_net(NetIR("+3V3", "power", [NetNode("U1", "3")]))
    return b


@needs_zig
def test_preemptive_multitasking_svc_pendsv_psp_basepri():
    image = cbuild.compile_c(MINI_RTOS, profile="stm32f4")
    twin = build_twin(rtos_board(), external_supplies={"+3V3": 3.3})
    twin.comp("U1").params["firmware"] = image
    twin.start()
    twin.kernel.run_until(400 * MS)

    assert twin.comp("U1").state != "faulted", \
        [v for _t, _n, v in twin.kernel.trace.logs()]
    t0 = twin.kernel.trace.transitions("T0_PIN")
    t1 = twin.kernel.trace.transitions("T1_PIN")

    # both tasks got scheduled and made steady progress
    assert len(t0) >= 20, f"task0 only toggled {len(t0)} times"
    assert len(t1) >= 20, f"task1 only toggled {len(t1)} times"

    # true interleaving: task1 toggles BETWEEN task0's toggles (round-robin)
    t0_times = [t for t, _ in t0]
    interleaved = sum(1 for t, _ in t1
                      if any(a < t < b for a, b in zip(t0_times, t0_times[1:])))
    assert interleaved >= 10

    # BASEPRI critical section: one long gap (~100 ms) where the scheduler
    # was masked and NEITHER task ran
    def max_gap(edges):
        ts = [t for t, _ in edges]
        return max((b - a for a, b in zip(ts, ts[1:])), default=0)
    assert max_gap(t0) > 50 * MS
    assert max_gap(t1) > 50 * MS

    # and both tasks resumed after the critical section
    assert t0[-1][0] > 300 * MS and t1[-1][0] > 300 * MS
