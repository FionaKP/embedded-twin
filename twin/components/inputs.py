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
        self.sig = self.require_net("1")
        self.other = self.net("2")
        self.pressed = False
        self.set_state("released")

    def press(self) -> None:
        self.pressed = True
        self.set_state("pressed")
        if self.other is not None and self.other.net_class == "ground":
            self.drive("1", Drive.low())
        elif self.other is not None and self.other.is_high:
            self.drive("1", Drive.high(self.other.voltage))
        else:
            self.drive("1", Drive.low())

    def release(self) -> None:
        self.pressed = False
        self.set_state("released")
        self.drive("1", Drive.release())
