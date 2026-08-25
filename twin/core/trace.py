"""Trace recorder: the virtual probe.

Every net transition, component state change, bus byte, and power sample is
recorded as (time_ns, kind, name, value). Post-run queries turn this into
waveforms, protocol decodes, and assertion evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator


@dataclass(frozen=True)
class TraceEntry:
    time: int
    kind: str   # net | state | uart | i2c | spi | power | log | event
    name: str
    value: Any


class TraceRecorder:
    def __init__(self) -> None:
        self.entries: list[TraceEntry] = []

    def record(self, time: int, kind: str, name: str, value: Any) -> None:
        self.entries.append(TraceEntry(time, kind, name, value))

    # -- queries ----------------------------------------------------------
    def select(self, kind: str | None = None, name: str | None = None,
               start: int = 0, end: int | None = None) -> Iterator[TraceEntry]:
        for e in self.entries:
            if kind is not None and e.kind != kind:
                continue
            if name is not None and e.name != name:
                continue
            if e.time < start or (end is not None and e.time > end):
                continue
            yield e

    def net_level_at(self, name: str, time: int) -> str | None:
        """Digital level of a net at an instant (last transition wins)."""
        level = None
        for e in self.select(kind="net", name=name, end=time):
            level = e.value["level"]
        return level

    def transitions(self, name: str) -> list[tuple[int, str]]:
        return [(e.time, e.value["level"]) for e in self.select(kind="net", name=name)]

    def uart_bytes(self, name: str, start: int = 0, end: int | None = None) -> bytes:
        return bytes(e.value for e in self.select(kind="uart", name=name, start=start, end=end))

    def logs(self) -> list[tuple[int, str, Any]]:
        return [(e.time, e.name, e.value) for e in self.select(kind="log")]
