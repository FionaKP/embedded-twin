"""Passive component models.

Behavioral, not SPICE (ADR-0002): a resistor ferries logic level across
itself at PULL strength (which is exactly how pull-ups/downs and series
resistors behave at system level); an LED watches its anode/cathode nets
and reports on/off + current draw.
"""
from __future__ import annotations

from ..core import Drive, Level, Strength
from .base import Component, register


@register("passive.resistor")
class Resistor(Component):
    """Passes the stronger side's level to the weaker side at PULL strength."""

    def start(self) -> None:
        self.a = self.net("1")
        self.b = self.net("2")
        if self.a is None or self.b is None:
            return  # dangling resistor: nothing to do
        self.a.listen(lambda _n: self._update())
        self.b.listen(lambda _n: self._update())
        self._update()

    def _side_strength(self, net, exclude_id):
        best = Strength.HIZ
        for drv_id, d in net._drives.items():
            if drv_id != exclude_id and d.strength > best:
                best = d.strength
        return best

    def _update(self) -> None:
        id_a, id_b = f"{self.ref}.1", f"{self.ref}.2"
        sa = self._side_strength(self.a, id_a)   # external drive on side A
        sb = self._side_strength(self.b, id_b)   # external drive on side B
        # stronger-driven side wins; propagate its level to the other side
        if sa >= sb and sa > Strength.HIZ:
            src, dst, src_id, dst_id = self.a, self.b, id_a, id_b
        elif sb > Strength.HIZ:
            src, dst, src_id, dst_id = self.b, self.a, id_b, id_a
        else:
            self.a.drive(id_a, Drive.release())
            self.b.drive(id_b, Drive.release())
            return
        src.drive(src_id, Drive.release())
        if src.level in (Level.HIGH, Level.LOW):
            dst.drive(dst_id, Drive(src.level, Strength.PULL, src.voltage))
        else:
            dst.drive(dst_id, Drive.release())


@register("passive.capacitor")
class Capacitor(Component):
    """Decoupling caps are structural at event resolution — no behavior."""


@register("passive.inductor")
class Inductor(Component):
    """Ferrite beads / chokes: pass-through, modeled like a resistor."""

    start = Resistor.start
    _side_strength = Resistor._side_strength
    _update = Resistor._update


@register("passive.diode")
class Diode(Component):
    """Structural in v0.1 (reverse-protection assumed correct)."""


@register("passive.led")
class Led(Component):
    """On when anode is HIGH and cathode LOW (through resistor chains).

    params: i_on_ma (default 5), vf (default 2.0)
    """

    def start(self) -> None:
        self.anode = self.require_net("A", "2")
        self.cathode = self.require_net("K", "1")
        self.anode.listen(lambda _n: self._update())
        self.cathode.listen(lambda _n: self._update())
        self.lit = False
        self.set_state("off")
        self._update()

    def _update(self) -> None:
        lit = self.anode.is_high and self.cathode.is_low
        if lit == self.lit:
            return
        self.lit = lit
        self.set_state("on" if lit else "off")
        # the LED's current is drawn from whatever rail feeds the anode path;
        # attribute it to the anode net's rail if known, else the anode net
        rail = self.params.get("rail", "")
        i_on = self.params.get("i_on_ma", 5.0) / 1000.0
        if rail:
            self.set_load(rail, i_on if lit else 0.0)


@register("connector.stub")
class ConnectorStub(Component):
    """Connectors/testpoints: no behavior, but their nets remain probeable."""
