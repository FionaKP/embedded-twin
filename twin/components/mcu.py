"""The MCU component: peripherals, power states, and firmware backends.

Profiles select the memory map the firmware was compiled for:
- "twin" (default): the generic TwinMCU map (twin/cpu/memmap.py) — polled
  I/O plus the CTRL_SLEEP_US fast-forward register.
- "stm32f4": real STM32F405/407 addresses (RCC/GPIO/USART + SysTick/NVIC
  interrupts + WFI-as-sleep) so register-level vendor firmware runs
  unmodified — see twin/cpu/profiles/stm32f4.py.

Behavioral Python firmware (McuApi) is profile-independent.
"""
from __future__ import annotations

from typing import Callable, Optional

from ..comm import Uart, I2CBus
from ..core import Drive
from ..core.kernel import US
from ..cpu import memmap as mm
from ..cpu.armv7m import Armv7mSystem
from ..cpu.unicorn_backend import UnicornMCU
from .base import Component, register

_POWER_PIN_NAMES = {"VDD", "VSS", "VCC", "GND", "VBAT", "VDDA", "VSSA",
                    "VREF", "VREF+", "VREF-", "NRST", "BOOT0"}


@register("mcu.cortex_m")
class CortexM(Component):
    """params:
    profile ("twin"|"stm32f4"), clock_hz (16 MHz), slice_us (1000),
    pinmap {pin_name: bit} (twin profile),
    uarts [{tx, rx, baud[, periph]}] (periph e.g. "USART2" for stm32f4),
    i2c [{scl, sda}], firmware (path to flat .bin or .elf),
    i_run_ua_per_mhz (150), i_sleep_ua (50)
    """

    def start(self) -> None:
        p = self.params
        self.profile_name = p.get("profile", "twin")
        self.clock_hz = int(p.get("clock_hz", 16_000_000))
        self.vdd = self.net("VDD", "VCC", "3V3")
        self.vdd_v = (self.vdd.voltage if self.vdd is not None and self.vdd.voltage else 3.3)
        self.rail = self.vdd.name if self.vdd is not None else ""

        # GPIO (twin profile: pin name <-> bit index; vendor profiles drive
        # nets by pin name directly through gpio_drive/gpio_release)
        self.pinmap: dict[str, int] = dict(p.get("pinmap", {}))
        if not self.pinmap:
            gpio_names = sorted(
                name for name in self.pin_nets
                if not name.isdigit() and name.upper() not in _POWER_PIN_NAMES)
            self.pinmap = {name: i for i, name in enumerate(gpio_names[:32])}
        self.bit_to_pin = {b: n for n, b in self.pinmap.items()}
        self.gpio_out = 0
        self.gpio_dir = 0

        # vendor profile (built before UARTs so ports can bind to it)
        self.profile = None
        self.system: Optional[Armv7mSystem] = None
        if self.profile_name != "twin":
            from ..cpu.profiles import PROFILES
            self.profile = PROFILES[self.profile_name](self)
            self.system = Armv7mSystem(self.clock_hz, log=self.log)

        # UARTs
        self.uarts: list[Uart] = []
        self._uart_rx: list[list[int]] = []
        for u in p.get("uarts", []):
            port = Uart(self.kernel, self.ref,
                        tx_net=self.net(u["tx"]) if u.get("tx") else None,
                        rx_net=self.net(u["rx"]) if u.get("rx") else None,
                        baud=int(u.get("baud", 115200)))
            fifo: list[int] = []
            self.uarts.append(port)
            self._uart_rx.append(fifo)
            usart = self.profile.usarts.get(u["periph"]) if (
                self.profile and u.get("periph")) else None
            if usart is not None:
                usart.port = port
                port.on_byte(usart.on_rx)
            else:
                port.on_byte(fifo.append)

        # I2C masters
        self.i2c_buses: list[I2CBus] = []
        for b in p.get("i2c", []):
            bus = I2CBus.on_nets(self.kernel, self.require_net(b["scl"]),
                                 self.require_net(b["sda"]))
            self.i2c_buses.append(bus)
            if self.profile and b.get("periph"):
                self.profile.named[b["periph"]].bus = bus

        # SPI masters
        from ..comm import SpiBus
        self.spi_buses: list[SpiBus] = []
        for s in p.get("spi", []):
            bus = SpiBus.on_nets(self.kernel, self.require_net(s["sck"]),
                                 self.net(s["mosi"]) if s.get("mosi") else None,
                                 self.net(s["miso"]) if s.get("miso") else None)
            self.spi_buses.append(bus)
            if self.profile and s.get("periph"):
                self.profile.named[s["periph"]].bus_ref = bus

        # power states
        self._i_run = self.clock_hz / 1e6 * p.get("i_run_ua_per_mhz", 150) * 1e-6
        self._i_sleep = p.get("i_sleep_ua", 50) * 1e-6
        self.set_power_state("run")

        # firmware backends
        self.backend: Optional[UnicornMCU] = None
        self._dbg_buf = bytearray()
        self._sleep_until = 0
        self._sleeping = False
        self.slice_ns = int(p.get("slice_us", 1000)) * US
        fw = p.get("firmware")
        if fw:
            self.load_firmware(fw)
        behavioral = p.get("behavioral")
        if behavioral is not None:
            self.load_behavioral(behavioral)
        self.set_state("running")

    # -- power ------------------------------------------------------------
    def set_power_state(self, state: str) -> None:
        current = {"run": self._i_run, "sleep": self._i_sleep, "off": 0.0}[state]
        if self.rail:
            self.set_load(self.rail, current)
        self.power_state = state
        self.kernel.trace.record(self.kernel.now, "state", f"{self.ref}.power", state)

    # -- GPIO -------------------------------------------------------------
    def gpio_write(self, pin: str, value: int) -> None:
        bit = self.pinmap[pin]
        if value:
            self.gpio_out |= (1 << bit)
        else:
            self.gpio_out &= ~(1 << bit)
        self.gpio_dir |= (1 << bit)
        self._drive_bit(bit)

    def gpio_read(self, pin: str) -> int:
        net = self.net(pin)
        return 1 if (net is not None and net.is_high) else 0

    def gpio_drive(self, pin: str, value: int) -> None:
        net = self.net(pin)
        if net is not None:
            net.drive(f"{self.ref}.{pin}",
                      Drive.high(self.vdd_v) if value else Drive.low())

    def gpio_release(self, pin: str) -> None:
        net = self.net(pin)
        if net is not None:
            net.drive(f"{self.ref}.{pin}", Drive.release())

    def _drive_bit(self, bit: int) -> None:
        pin = self.bit_to_pin.get(bit)
        if pin is None:
            return
        if not (self.gpio_dir >> bit) & 1:
            self.gpio_release(pin)
        elif (self.gpio_out >> bit) & 1:
            self.gpio_drive(pin, 1)
        else:
            self.gpio_drive(pin, 0)

    def _gpio_in_word(self) -> int:
        word = 0
        for name, bit in self.pinmap.items():
            if self.gpio_read(name):
                word |= (1 << bit)
        return word

    # -- interrupts (vendor profiles) --------------------------------------
    def pend_irq(self, irq: int) -> None:
        if self.system is not None and self.system.pend_irq(irq) and self._sleeping:
            self._wake_now()

    def _wake_now(self) -> None:
        if self._sleeping:
            self._sleeping = False
            self.set_power_state("run")
            self.kernel.schedule(0, self._run_slice)

    # -- Unicorn firmware -------------------------------------------------
    def load_firmware(self, fw) -> None:
        if self.profile is not None:
            self.backend = UnicornMCU(
                self.clock_hz, self._bus_read, self._bus_write,
                slice_ns=self.slice_ns,
                flash=(self.profile.flash_base, self.profile.flash_size),
                ram_regions=self.profile.ram_regions,
                periph_window=(self.profile.periph_base, self.profile.periph_size),
                system=self.system, log=self.log)
        else:
            self.backend = UnicornMCU(self.clock_hz, self._twin_read,
                                      self._twin_write, slice_ns=self.slice_ns)
        if isinstance(fw, (bytes, bytearray)):
            self.backend.load_bin(bytes(fw))
        elif str(fw).endswith(".elf"):
            self.backend.load_elf(str(fw))
        else:
            self.backend.load_bin(open(fw, "rb").read())
        self.kernel.schedule(0, self._run_slice)

    def _run_slice(self) -> None:
        be = self.backend
        if be is None or be.halted:
            return
        if self.system is not None:
            self.system.advance(self.kernel.now)
        if self.profile is not None:
            for periph in self.profile.bus.periphs:
                adv = getattr(periph, "advance", None)
                if adv is not None:
                    adv(self.kernel.now)
        # keep SysTick jitter below one period by shortening slices
        ns = self.slice_ns
        if self.system is not None and self.system.systick.enabled:
            ns = min(ns, self.system.systick.period_ns())
        try:
            status = be.run_slice(ns)
        except RuntimeError as e:
            self.log(f"FAULT: {e}")
            self.set_state("faulted")
            self.set_power_state("off")
            return

        if status == "halted":
            self.set_state("halted")
            self.set_power_state("off")
        elif status == "wfi":
            if self.backend.can_wake():
                # an interrupt is already pending and unmasked: no sleep
                self.kernel.schedule(0, self._run_slice)
                return
            wakes = [self.system.next_wake_ns()] if self.system else []
            if self.profile is not None:
                for periph in self.profile.bus.periphs:
                    nev = getattr(periph, "next_event_ns", None)
                    if nev is not None:
                        wakes.append(nev(self.kernel.now))
            wakes = [w for w in wakes if w is not None]
            wake = min(wakes) if wakes else None
            self._sleeping = True
            self.set_power_state("sleep")
            if wake is not None:
                self.kernel.schedule_at(max(wake, self.kernel.now), self._wake_now)
            # else: stay asleep until an interrupt (e.g. UART RX) pends
        elif status == "reset":
            self.log("MCU reset (SYSRESETREQ)")
            self.kernel.schedule(0, self._run_slice)
        elif self._sleep_until > self.kernel.now:   # legacy CTRL_SLEEP_US
            self.set_power_state("sleep")
            self.kernel.schedule_at(self._sleep_until, self._legacy_wake)
        else:
            self.kernel.schedule(ns, self._run_slice)

    def _legacy_wake(self) -> None:
        self.set_power_state("run")
        self._run_slice()

    # -- peripheral dispatch: vendor profile bus ---------------------------
    def _bus_read(self, addr: int, size: int) -> int:
        return self.profile.bus.read(addr, size)

    def _bus_write(self, addr: int, value: int, size: int) -> None:
        self.profile.bus.write(addr, value, size)

    # -- peripheral dispatch: legacy TwinMCU map ---------------------------
    def _twin_read(self, addr: int, size: int) -> int:
        if addr == mm.GPIO_IN:
            return self._gpio_in_word()
        if addr == mm.GPIO_OUT:
            return self.gpio_out
        if addr == mm.GPIO_DIR:
            return self.gpio_dir
        if addr == mm.UART0_DR:
            return self._uart_rx[0].pop(0) if self._uart_rx and self._uart_rx[0] else 0
        if addr == mm.UART0_SR:
            rxne = 1 if (self._uart_rx and self._uart_rx[0]) else 0
            return rxne | 0b10  # TXE always
        if addr == mm.TIM_CNT_US:
            return (self.kernel.now // US) & 0xFFFFFFFF
        return 0

    def _twin_write(self, addr: int, value: int, size: int) -> None:
        if addr == mm.GPIO_OUT:
            changed = self.gpio_out ^ value
            self.gpio_out = value
            for bit in range(32):
                if (changed >> bit) & 1:
                    self._drive_bit(bit)
        elif addr == mm.GPIO_DIR:
            changed = self.gpio_dir ^ value
            self.gpio_dir = value
            for bit in range(32):
                if (changed >> bit) & 1:
                    self._drive_bit(bit)
        elif addr == mm.GPIO_SET:
            self._twin_write(mm.GPIO_OUT, self.gpio_out | value, size)
        elif addr == mm.GPIO_CLR:
            self._twin_write(mm.GPIO_OUT, self.gpio_out & ~value, size)
        elif addr == mm.UART0_DR:
            if self.uarts:
                self.uarts[0].send(bytes([value & 0xFF]))
        elif addr == mm.CTRL_SLEEP_US:
            self._sleep_until = self.kernel.now + value * US
            if self.backend:
                self.backend.skip_current = True
                self.backend.stop()
        elif addr == mm.CTRL_EXIT:
            if self.backend:
                self.backend.halted = True
                self.backend.stop()
        elif addr == mm.CTRL_DBG:
            if value == 0x0A:
                self.log(self._dbg_buf.decode(errors="replace"))
                self._dbg_buf.clear()
            else:
                self._dbg_buf.append(value & 0xFF)

    # -- behavioral firmware ----------------------------------------------
    def load_behavioral(self, fn: Callable[["McuApi"], None]) -> None:
        self.api = McuApi(self)
        self.kernel.schedule(0, fn, self.api)


class McuApi:
    """What behavioral firmware programs against — mirrors a thin HAL."""

    def __init__(self, mcu: CortexM):
        self._mcu = mcu
        self.kernel = mcu.kernel

    def gpio_write(self, pin: str, value: int) -> None:
        self._mcu.gpio_write(pin, value)

    def gpio_read(self, pin: str) -> int:
        return self._mcu.gpio_read(pin)

    def uart(self, idx: int = 0) -> Uart:
        return self._mcu.uarts[idx]

    def i2c_write(self, bus: int, addr: int, data: bytes) -> bool:
        return self._mcu.i2c_buses[bus].write(addr, data)

    def i2c_read(self, bus: int, addr: int, n: int) -> Optional[bytes]:
        return self._mcu.i2c_buses[bus].read(addr, n)

    def after(self, delay_us: float, fn: Callable) -> None:
        self.kernel.schedule(int(delay_us * US), fn)

    def every(self, period_us: float, fn: Callable) -> None:
        def tick():
            fn()
            self.kernel.schedule(int(period_us * US), tick)
        self.kernel.schedule(int(period_us * US), tick)

    def sleep(self) -> None:
        self._mcu.set_power_state("sleep")

    def wake(self) -> None:
        self._mcu.set_power_state("run")

    def log(self, msg: str) -> None:
        self._mcu.log(msg)

    @property
    def now_us(self) -> float:
        return self.kernel.now / US
