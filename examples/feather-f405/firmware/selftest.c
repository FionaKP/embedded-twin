/* Self-test firmware for the Adafruit Feather STM32F405 Express — running
 * against the board's REAL Eagle schematic in embedded-twin.
 *
 * - reads the SPI flash JEDEC ID over SPI1 (PB3/PB4/PB5, CS on PA15)
 * - samples the battery voltage divider (V_DIV on PA3, ADC ch3)
 * - blinks D13 (PC1): 200 ms when the flash checks out, 100 ms error blink
 * - raises D12 (PC2) when a battery above 3.0 V is present
 * - sleeps in WFI between 100 ms SysTick ticks
 */
#include <stdint.h>
#define REG(a) (*(volatile uint32_t*)(a))
#define RCC_AHB1ENR  REG(0x40023830)
#define RCC_APB2ENR  REG(0x40023844)
#define GPIOA_MODER  REG(0x40020000)
#define GPIOA_BSRR   REG(0x40020018)
#define GPIOC_MODER  REG(0x40020800)
#define GPIOC_ODR    REG(0x40020814)
#define GPIOC_BSRR   REG(0x40020818)
#define SPI1_SR      REG(0x40013008)
#define SPI1_DR      REG(0x4001300C)
#define ADC1_SR      REG(0x40012000)
#define ADC1_CR2     REG(0x40012008)
#define ADC1_SQR3    REG(0x40012034)
#define ADC1_DR      REG(0x4001204C)
#define SYST_CSR     REG(0xE000E010)
#define SYST_RVR     REG(0xE000E014)

extern uint32_t _estack;
void Reset_Handler(void);
void SysTick_Handler(void);
__attribute__((section(".vectors")))
const void* vectors[64] = {
    [0] = &_estack, [1] = Reset_Handler, [15] = SysTick_Handler,
};

static volatile uint32_t tick;
void SysTick_Handler(void) { tick++; }

static uint32_t spi1_xfer(uint32_t b) {
    SPI1_DR = b;
    while (!(SPI1_SR & 1u)) {}
    return SPI1_DR;
}

void Reset_Handler(void) {
    RCC_AHB1ENR |= 0x7u;                     /* GPIOA+B+C clocks */
    RCC_APB2ENR |= (1u << 12) | (1u << 8);   /* SPI1 + ADC1 */

    GPIOC_MODER = (GPIOC_MODER & ~((3u << 2) | (3u << 4)))
                | (1u << 2) | (1u << 4);     /* PC1 (D13), PC2 (D12) out */
    GPIOA_BSRR = (1u << 15);                 /* flash CS idle high */
    GPIOA_MODER = (GPIOA_MODER & ~(3u << 30)) | (1u << 30);  /* PA15 out */

    /* --- flash JEDEC ID (GD25Q16 = C8 40 15) --- */
    GPIOA_BSRR = (1u << 15) << 16;           /* CS low */
    spi1_xfer(0x9F);
    uint32_t m = spi1_xfer(0), t = spi1_xfer(0), c = spi1_xfer(0);
    GPIOA_BSRR = (1u << 15);                 /* CS high */
    uint32_t flash_ok = (m == 0xC8 && t == 0x40 && c == 0x15);

    SYST_RVR = 1600000 - 1;                  /* 100 ms tick @ 16 MHz */
    SYST_CSR = 7;

    uint32_t period = flash_ok ? 2 : 1;      /* 200 ms good / 100 ms error */
    uint32_t last = 0;
    for (;;) {
        __asm volatile("wfi");
        if (tick - last >= period) {
            last = tick;
            GPIOC_ODR ^= (1u << 1);          /* D13 blink */
            ADC1_SQR3 = 3;                   /* V_DIV = PA3 = ch3 */
            ADC1_CR2 = (1u << 30) | 1u;
            while (!(ADC1_SR & 2u)) {}
            uint32_t raw = ADC1_DR;
            if (raw > 3723)                  /* > 3.0 V */
                GPIOC_BSRR = (1u << 2);      /* D12: battery present */
            else
                GPIOC_BSRR = (1u << 2) << 16;
        }
    }
}
