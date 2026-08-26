/* Real STM32F4 register-level firmware — compiled by the scenario runner
 * with the zig toolchain, executed unmodified on the twin's stm32f4 profile.
 *
 * Blinks PA5 at 250 ms; while the user button (PA0, active low) is held,
 * blinks at 50 ms. Sleeps in WFI between SysTick interrupts.
 */
#include <stdint.h>
#define REG(a) (*(volatile uint32_t*)(a))
#define RCC_AHB1ENR   REG(0x40023830)
#define GPIOA_MODER   REG(0x40020000)
#define GPIOA_IDR     REG(0x40020010)
#define GPIOA_ODR     REG(0x40020014)
#define SYST_CSR      REG(0xE000E010)
#define SYST_RVR      REG(0xE000E014)
#define SYST_CVR      REG(0xE000E018)

extern uint32_t _estack;
void Reset_Handler(void);
void SysTick_Handler(void);

__attribute__((section(".vectors")))
const void* vectors[64] = {
    [0] = &_estack,
    [1] = Reset_Handler,
    [15] = SysTick_Handler,
};

static volatile uint32_t tick_ms;

void SysTick_Handler(void) { tick_ms++; }

void Reset_Handler(void) {
    RCC_AHB1ENR |= 1u;                                       /* GPIOA clock */
    GPIOA_MODER = (GPIOA_MODER & ~(3u << 10)) | (1u << 10);  /* PA5 out, PA0 in */
    SYST_RVR = 16000 - 1;                                    /* 1 ms @ 16 MHz */
    SYST_CVR = 0;
    SYST_CSR = 7;                                            /* en | int | core */

    uint32_t last_toggle = 0;
    for (;;) {
        __asm volatile("wfi");
        uint32_t period = (GPIOA_IDR & 1u) ? 250u : 50u;     /* PA0 low = fast */
        if (tick_ms - last_toggle >= period) {
            last_toggle = tick_ms;
            GPIOA_ODR ^= (1u << 5);
        }
    }
}
