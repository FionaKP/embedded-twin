"""TwinMCU memory map: the generic Cortex-M profile firmware compiles against.

Vendor-accurate maps (STM32, nRF52) are v0.2 — they are address tables, not
new machinery. Everything here is polled I/O plus a SLEEP fast-forward
register; interrupt/NVIC fidelity is roadmapped.
"""

FLASH_BASE = 0x0800_0000
FLASH_SIZE = 256 * 1024
RAM_BASE = 0x2000_0000
RAM_SIZE = 64 * 1024
INITIAL_SP = RAM_BASE + RAM_SIZE

PERIPH_BASE = 0x4000_0000
PERIPH_SIZE = 0x1_0000

# GPIO block (32 pins)
GPIO_OUT = 0x4000_0000   # RW output latch
GPIO_IN = 0x4000_0004    # RO pin states
GPIO_DIR = 0x4000_0008   # RW 1=output
GPIO_SET = 0x4000_000C   # WO set bits
GPIO_CLR = 0x4000_0010   # WO clear bits

# UART0 (first uart in the MCU's uart list)
UART0_DR = 0x4000_1000   # W: tx byte / R: pop rx byte
UART0_SR = 0x4000_1004   # bit0 RXNE, bit1 TXE (always set)

# Timer
TIM_CNT_US = 0x4000_2000  # RO: sim time in microseconds (32-bit, wraps)

# Control
CTRL_SLEEP_US = 0x4000_3000  # W: sleep n microseconds (low-power fast-forward)
CTRL_EXIT = 0x4000_3004      # W: halt firmware (test/scenario end)
CTRL_DBG = 0x4000_3008       # W: debug char out (newline flushes a log line)
