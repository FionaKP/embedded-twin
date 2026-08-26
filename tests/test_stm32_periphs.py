"""STM32F4 peripheral coverage: SPI, I2C, ADC, TIM, EXTI — each exercised
by real C firmware talking to board-level component models."""
import pytest

from twin.build import build_twin
from twin.core import Drive
from twin.core.kernel import MS, SEC
from twin.cpu import cbuild
from twin.ir import BoardIR, ComponentIR, NetIR, NetNode

needs_zig = pytest.mark.skipif(not cbuild.zig_available(),
                               reason="ziglang not installed")

HEADER = r"""
#include <stdint.h>
#define REG(a) (*(volatile uint32_t*)(a))
#define RCC_AHB1ENR   REG(0x40023830)
#define RCC_APB1ENR   REG(0x40023840)
#define RCC_APB2ENR   REG(0x40023844)
#define GPIOA_MODER   REG(0x40020000)
#define GPIOA_IDR     REG(0x40020010)
#define GPIOA_ODR     REG(0x40020014)
#define GPIOA_BSRR    REG(0x40020018)
#define SPI1_SR       REG(0x40013008)
#define SPI1_DR       REG(0x4001300C)
#define I2C1_CR1      REG(0x40005400)
#define I2C1_DR       REG(0x40005410)
#define I2C1_SR1      REG(0x40005414)
#define I2C1_SR2      REG(0x40005418)
#define ADC1_SR       REG(0x40012000)
#define ADC1_CR2      REG(0x40012008)
#define ADC1_SQR3     REG(0x40012034)
#define ADC1_DR       REG(0x4001204C)
#define TIM2_CR1      REG(0x40000000)
#define TIM2_DIER     REG(0x4000000C)
#define TIM2_SR       REG(0x40000010)
#define TIM2_PSC      REG(0x40000028)
#define TIM2_ARR      REG(0x4000002C)
#define SYSCFG_EXTICR1 REG(0x40013808)
#define EXTI_IMR      REG(0x40013C00)
#define EXTI_RTSR     REG(0x40013C08)
#define EXTI_FTSR     REG(0x40013C0C)
#define EXTI_PR       REG(0x40013C14)
#define NVIC_ISER0    REG(0xE000E100)
extern uint32_t _estack;
void Reset_Handler(void);
void TIM2_IRQHandler(void);
void EXTI0_IRQHandler(void);
static void Default_Handler(void) { for(;;){} }
__attribute__((section(".vectors")))
const void* vectors[64] = {
    [0] = &_estack, [1] = Reset_Handler, [3] = Default_Handler,
    [16 + 28] = TIM2_IRQHandler, [16 + 6] = EXTI0_IRQHandler,
};
#define FLAG_ON()  (GPIOA_BSRR = (1u << 5))
#define STUB_TIM2  void TIM2_IRQHandler(void) {}
#define STUB_EXTI0 void EXTI0_IRQHandler(void) {}
static void gpio_init_pa5_out(void) {
    RCC_AHB1ENR |= 1u;
    GPIOA_MODER = (GPIOA_MODER & ~(3u << 10)) | (1u << 10);
}
"""


def board(extra_comps=(), extra_nets=(), mcu_params=None) -> BoardIR:
    b = BoardIR(name="periphboard")
    params = {"profile": "stm32f4", "clock_hz": 16_000_000, "slice_us": 1000}
    params.update(mcu_params or {})
    b.add_component(ComponentIR(
        ref="U1", model="mcu.cortex_m", params=params,
        pins={"1": "PA5", "2": "PA0", "3": "PA1", "4": "PA4",
              "5": "PB3", "6": "PB4", "7": "PB5",
              "8": "PB6", "9": "PB7", "10": "VDD"}))
    b.add_net(NetIR("FLAG", "signal", [NetNode("U1", "1")]))       # PA5
    b.add_net(NetIR("BTN", "signal", [NetNode("U1", "2")]))        # PA0
    b.add_net(NetIR("AIN", "signal", [NetNode("U1", "3")]))        # PA1
    b.add_net(NetIR("ACC_CS", "signal", [NetNode("U1", "4")]))     # PA4
    b.add_net(NetIR("SCK", "signal", [NetNode("U1", "5")]))        # PB3
    b.add_net(NetIR("MISO", "signal", [NetNode("U1", "6")]))       # PB4
    b.add_net(NetIR("MOSI", "signal", [NetNode("U1", "7")]))       # PB5
    b.add_net(NetIR("SCL", "signal", [NetNode("U1", "8")]))        # PB6
    b.add_net(NetIR("SDA", "signal", [NetNode("U1", "9")]))        # PB7
    b.add_net(NetIR("+3V3", "power", [NetNode("U1", "10")]))
    for c in extra_comps:
        b.add_component(c)
    for n in extra_nets:
        b.nets[n.name].nodes.extend(n.nodes)
    return b


def run_fw(b: BoardIR, code: str, run_s: float = 0.5):
    image = cbuild.compile_c(HEADER + code, profile="stm32f4")
    twin = build_twin(b, external_supplies={"+3V3": 3.3})
    twin.comp("U1").params["firmware"] = image
    twin.start()
    twin.kernel.run_until(int(run_s * SEC))
    return twin


@needs_zig
def test_spi_reads_accelerometer_who_am_i():
    accel = ComponentIR(ref="ACC1", model="sensor.accel_spi",
                        pins={"1": "SCK", "2": "MOSI", "3": "MISO",
                              "4": "CS", "5": "VDD"})
    b = board(extra_comps=[accel],
              mcu_params={"spi": [{"periph": "SPI1", "sck": "PB3",
                                   "mosi": "PB5", "miso": "PB4"}]})
    for net, pin in (("SCK", "1"), ("MOSI", "2"), ("MISO", "3"),
                     ("ACC_CS", "4"), ("+3V3", "5")):
        b.nets[net].nodes.append(NetNode("ACC1", pin))
    twin = run_fw(b, r"""
STUB_TIM2
STUB_EXTI0
void Reset_Handler(void) {
    gpio_init_pa5_out();
    GPIOA_ODR |= (1u << 4);                              /* CS idle high */
    GPIOA_MODER = (GPIOA_MODER & ~(3u << 8)) | (1u << 8);/* PA4 output */
    RCC_APB2ENR |= (1u << 12);                           /* SPI1 clock */

    GPIOA_BSRR = (1u << 4) << 16;                        /* CS low */
    SPI1_DR = 0x8F;                                      /* read 0x0F */
    (void)SPI1_DR;                                       /* dummy */
    SPI1_DR = 0x00;
    uint32_t who = SPI1_DR;
    GPIOA_BSRR = (1u << 4);                              /* CS high */
    if (who == 0x33) FLAG_ON();
    for (;;) __asm volatile("wfi");
}
""")
    assert twin.net("FLAG").is_high
    spi_events = list(twin.kernel.trace.select(kind="spi"))
    assert any(e.value["miso"] == 0x33 for e in spi_events)


@needs_zig
def test_i2c_polled_master_reads_temp_sensor():
    temp = ComponentIR(ref="U3", model="sensor.temp_i2c",
                       pins={"1": "SCL", "5": "SDA", "4": "VDD"})
    b = board(extra_comps=[temp],
              mcu_params={"i2c": [{"periph": "I2C1", "scl": "PB6", "sda": "PB7"}]})
    b.nets["SCL"].nodes.append(NetNode("U3", "1"))
    b.nets["SDA"].nodes.append(NetNode("U3", "5"))
    b.nets["+3V3"].nodes.append(NetNode("U3", "4"))
    twin = run_fw(b, r"""
STUB_TIM2
STUB_EXTI0
void Reset_Handler(void) {
    gpio_init_pa5_out();
    RCC_APB1ENR |= (1u << 21);                 /* I2C1 clock */

    I2C1_CR1 = (1u << 8);                      /* START */
    while (!(I2C1_SR1 & 1u)) {}                /* SB */
    I2C1_DR = (0x48 << 1);                     /* addr, write */
    while (!(I2C1_SR1 & 2u)) {}                /* ADDR */
    (void)I2C1_SR2;
    I2C1_DR = 0x00;                            /* pointer: temperature reg */
    I2C1_CR1 = (1u << 9);                      /* STOP: commit */

    I2C1_CR1 = (1u << 8);                      /* repeated START */
    while (!(I2C1_SR1 & 1u)) {}
    I2C1_DR = (0x48 << 1) | 1u;                /* addr, read */
    while (!(I2C1_SR1 & 2u)) {}
    (void)I2C1_SR2;
    while (!(I2C1_SR1 & (1u << 6))) {}         /* RXNE */
    uint32_t hi = I2C1_DR;
    uint32_t lo = I2C1_DR;
    I2C1_CR1 = (1u << 9);                      /* STOP */

    int32_t raw = (int32_t)((hi << 8) | lo) >> 4;
    if (raw == 25 * 16) FLAG_ON();             /* 25.0 C in TMP102 format */
    for (;;) __asm volatile("wfi");
}
""")
    assert twin.net("FLAG").is_high


@needs_zig
def test_adc_samples_net_voltage():
    b = board()
    image = cbuild.compile_c(HEADER + r"""
STUB_TIM2
STUB_EXTI0
void Reset_Handler(void) {
    gpio_init_pa5_out();
    RCC_APB2ENR |= (1u << 8);                  /* ADC1 clock */
    ADC1_SQR3 = 1;                             /* channel 1 = PA1 */
    ADC1_CR2 = 1u;                             /* ADON */
    ADC1_CR2 = (1u << 30) | 1u;                /* SWSTART */
    while (!(ADC1_SR & 2u)) {}                 /* EOC */
    uint32_t code = ADC1_DR;
    if (code > 1950 && code < 2150) FLAG_ON(); /* ~1.65 V of 3.3 V */
    for (;;) __asm volatile("wfi");
}
""", profile="stm32f4")
    twin = build_twin(b, external_supplies={"+3V3": 3.3})
    twin.net("AIN").drive("dac", Drive.analog(1.65))
    twin.comp("U1").params["firmware"] = image
    twin.start()
    twin.kernel.run_until(int(0.2 * SEC))
    assert twin.net("FLAG").is_high


@needs_zig
def test_tim2_periodic_interrupt_blink_with_wfi():
    twin = run_fw(board(), r"""
STUB_EXTI0
void TIM2_IRQHandler(void) {
    TIM2_SR = 0;                               /* clear UIF */
    GPIOA_ODR ^= (1u << 5);
}
void Reset_Handler(void) {
    gpio_init_pa5_out();
    RCC_APB1ENR |= 1u;                         /* TIM2 clock */
    TIM2_PSC = 1600 - 1;                       /* 10 kHz */
    TIM2_ARR = 500 - 1;                        /* 50 ms update */
    TIM2_DIER = 1u;                            /* UIE */
    NVIC_ISER0 = (1u << 28);
    TIM2_CR1 = 1u;                             /* CEN */
    for (;;) __asm volatile("wfi");
}
""", run_s=1.0)
    edges = twin.kernel.trace.transitions("FLAG")
    assert 17 <= len(edges) <= 22, f"edges: {len(edges)}"  # ~20 in 1 s
    # timer drives WFI wakes: the MCU sleeps between interrupts
    power = [e.value for e in twin.kernel.trace.select(kind="state",
                                                       name="U1.power")]
    assert "sleep" in power


@needs_zig
def test_exti_button_interrupt_wakes_sleeping_mcu():
    b = board()
    image = cbuild.compile_c(HEADER + r"""
STUB_TIM2
void EXTI0_IRQHandler(void) {
    EXTI_PR = 1u;                              /* clear line 0 */
    FLAG_ON();
}
void Reset_Handler(void) {
    gpio_init_pa5_out();
    RCC_APB2ENR |= (1u << 14);                 /* SYSCFG clock */
    SYSCFG_EXTICR1 = 0;                        /* line 0 <- port A */
    EXTI_FTSR = 1u;                            /* falling edge (button press) */
    EXTI_IMR = 1u;
    NVIC_ISER0 = (1u << 6);                    /* EXTI0 */
    for (;;) __asm volatile("wfi");            /* sleep until pressed */
}
""", profile="stm32f4")
    twin = build_twin(b, external_supplies={"+3V3": 3.3})
    twin.net("BTN").drive("pullup", Drive.pull_up(3.3))
    twin.comp("U1").params["firmware"] = image
    twin.start()
    twin.kernel.schedule_at(50 * MS, twin.net("BTN").drive, "press", Drive.low())
    twin.kernel.run_until(200 * MS)
    assert twin.net("FLAG").is_high
    t_on = twin.kernel.trace.transitions("FLAG")[-1][0]
    assert 50 * MS <= t_on <= 55 * MS          # woke from deep WFI on the edge
