"""Discrete-event simulation kernel.

Time is an integer number of nanoseconds since simulation start. Determinism
is a hard requirement: events at the same timestamp dispatch in scheduling
order, and all randomness must come from ``SimKernel.rng`` (seeded).
"""
from __future__ import annotations

import heapq
import random
from dataclasses import dataclass, field
from typing import Any, Callable

from .trace import TraceRecorder

NS = 1
US = 1_000
MS = 1_000_000
SEC = 1_000_000_000


@dataclass(order=True)
class Event:
    time: int
    seq: int
    fn: Callable[..., Any] = field(compare=False)
    args: tuple = field(compare=False, default=())
    cancelled: bool = field(compare=False, default=False)

    def cancel(self) -> None:
        self.cancelled = True


class SimKernel:
    def __init__(self, seed: int = 0):
        self.now: int = 0
        self.rng = random.Random(seed)
        self.trace = TraceRecorder()
        self._queue: list[Event] = []
        self._seq = 0
        # components register themselves for lifecycle hooks (start/finalize)
        self.components: dict[str, Any] = {}

    # -- scheduling -------------------------------------------------------
    def schedule(self, delay_ns: int, fn: Callable, *args) -> Event:
        if delay_ns < 0:
            raise ValueError(f"cannot schedule in the past (delay={delay_ns})")
        ev = Event(self.now + int(delay_ns), self._seq, fn, args)
        self._seq += 1
        heapq.heappush(self._queue, ev)
        return ev

    def schedule_at(self, time_ns: int, fn: Callable, *args) -> Event:
        return self.schedule(time_ns - self.now, fn, *args)

    # -- execution --------------------------------------------------------
    def step(self) -> bool:
        """Dispatch the next event. Returns False when the queue is empty."""
        while self._queue:
            ev = heapq.heappop(self._queue)
            if ev.cancelled:
                continue
            self.now = ev.time
            ev.fn(*ev.args)
            return True
        return False

    def run_until(self, t_ns: int) -> None:
        """Run all events with time <= t_ns, then advance the clock to t_ns."""
        while self._queue:
            nxt = self._queue[0]
            if nxt.cancelled:
                heapq.heappop(self._queue)
                continue
            if nxt.time > t_ns:
                break
            self.step()
        self.now = max(self.now, t_ns)

    def run(self, max_events: int = 1_000_000) -> int:
        """Run until the queue drains (or the safety cap). Returns events run."""
        n = 0
        while n < max_events and self.step():
            n += 1
        return n

    # -- component lifecycle ---------------------------------------------
    def register(self, name: str, component: Any) -> None:
        if name in self.components:
            raise ValueError(f"duplicate component name {name!r}")
        self.components[name] = component

    def start(self) -> None:
        """Call start() on every registered component (power-on)."""
        for comp in self.components.values():
            start = getattr(comp, "start", None)
            if start:
                start()
