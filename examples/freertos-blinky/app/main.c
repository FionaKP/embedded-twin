/* Unmodified FreeRTOS (ARM_CM3 port) on the embedded-twin STM32F4 profile.
 * Two tasks blink PA5 (100 ms) and PA6 (250 ms) via vTaskDelay.
 */
#include <stdint.h>
#include "FreeRTOS.h"
#include "task.h"

#define REG(a) (*(volatile uint32_t*)(a))
#define RCC_AHB1ENR REG(0x40023830)
#define GPIOA_MODER REG(0x40020000)
#define GPIOA_ODR   REG(0x40020014)

extern uint32_t _estack;
void Reset_Handler(void);                 /* provided by cbuild crt0 */
void vPortSVCHandler(void);
void xPortPendSVHandler(void);
void xPortSysTickHandler(void);
static void Default_Handler(void) { for (;;) {} }

__attribute__((section(".vectors")))
const void* vectors[64] = {
    [0]  = &_estack,
    [1]  = Reset_Handler,
    [3]  = Default_Handler,               /* HardFault */
    [11] = vPortSVCHandler,
    [14] = xPortPendSVHandler,
    [15] = xPortSysTickHandler,
};

static void blink(void *arg) {
    const uint32_t bit = 1u << (uint32_t)(uintptr_t)arg;
    const TickType_t period =
        ((uintptr_t)arg == 5) ? pdMS_TO_TICKS(100) : pdMS_TO_TICKS(250);
    for (;;) {
        GPIOA_ODR ^= bit;
        vTaskDelay(period);
    }
}

int main(void) {
    RCC_AHB1ENR |= 1u;
    GPIOA_MODER = (GPIOA_MODER & ~((3u << 10) | (3u << 12)))
                | (1u << 10) | (1u << 12);          /* PA5, PA6 outputs */
    xTaskCreate(blink, "led5", configMINIMAL_STACK_SIZE, (void*)5, 2, NULL);
    xTaskCreate(blink, "led6", configMINIMAL_STACK_SIZE, (void*)6, 1, NULL);
    vTaskStartScheduler();
    for (;;) {}                                     /* never reached */
}
