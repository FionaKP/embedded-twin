"""Human/physical input models."""
from __future__ import annotations

from ..core import Drive
from .base import Component, register


@register("input.button")
class Button(Component):
    """Momentary switch to ground (typical wiring: pin 1 = signal, pin 2 = GND).

    Scenario stimuli call press()/release(); a press shorts the signal net
    to the other side's level at STRONG strength.
    """

    def start(self) -> None:
        self.sig = self.require_net("1", "P", "P1", "A")
        self.other = self.net("2", "S", "S1", "B")
        self._sig_pin = next(p for p in ("1", "P", "P1", "A")
                             if self.net(p) is self.sig)
        self.pressed = False
        self.set_state("released")

    def press(self) -> None:
        self.pressed = True
        self.set_state("pressed")
        if self.other is not None and self.other.net_class == "ground":
            self.drive(self._sig_pin, Drive.low())
        elif self.other is not None and self.other.is_high:
            self.drive(self._sig_pin, Drive.high(self.other.voltage))
        else:
            self.drive(self._sig_pin, Drive.low())

    def release(self) -> None:
        self.pressed = False
        self.set_state("released")
        self.drive(self._sig_pin, Drive.release())
