"""Scenario runner: board + firmware + environment + stimuli -> verdicts.

The runner is the integration point of every layer. It stays deliberately
dumb: schedule events, run the kernel, hand the trace to the assertion
engine. All intelligence lives in the layers below.
"""
from __future__ import annotations

import importlib
import sys
from typing import Any

from ..build import BoardTwin, build_twin
from ..core import Drive
from ..env import Environment, Position, GnssWorld, CellularWorld, Tower, BleWorld
from ..ingest import parse_kicad_netlist, merge_bom, bind_models
from ..ir import BoardIR
from .assertions import evaluate
from .lockfile import make_lock
from .spec import Scenario, load_scenario, parse_time


class ScenarioRun:
    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.twin: BoardTwin | None = None
        self.env: Environment | None = None
        self.results: list[dict] = []
        self.bind_report: dict = {}

    # -- setup ------------------------------------------------------------
    def _load_board(self) -> BoardIR:
        cfg = self.scenario.raw.get("board", {})
        for d in cfg.get("model_dirs", []) or []:
            from ..factory.loader import load_model_dir
            load_model_dir(self.scenario.resolve(d))
        if "ir" in cfg:
            board = BoardIR.from_json(self.scenario.resolve(cfg["ir"]).read_text())
        else:
            board = parse_kicad_netlist(self.scenario.resolve(cfg["netlist"]))
            if cfg.get("bom"):
                merge_bom(board, self.scenario.resolve(cfg["bom"]))
        self.bind_report = bind_models(board)
        for ref, overrides in (cfg.get("components") or {}).items():
            comp = board.components[ref]
            if "model" in overrides:
                comp.model = overrides["model"]
            comp.params.update(overrides.get("params", {}))
        return board

    def _setup_environment(self) -> None:
        cfg = self.scenario.raw.get("environment", {}) or {}
        kernel = self.twin.kernel
        pos_cfg = cfg.get("position", {})
        env = Environment(kernel, start_utc=cfg.get("start_utc", "2026-08-25T12:00:00"),
                          position=Position(pos_cfg.get("lat", 42.2626),
                                            pos_cfg.get("lon", -71.8023),
                                            pos_cfg.get("alt_m", 0.0)))
        v = cfg.get("velocity_mps")
        if v:
            env.velocity_mps = (v.get("east", 0.0), v.get("north", 0.0))
        g = cfg.get("gnss", {}) or {}
        if g.get("tle"):
            env.gnss = GnssWorld.from_tle(str(self.scenario.resolve(g["tle"])),
                                          condition=g.get("condition", "open_sky"))
        else:
            env.gnss = GnssWorld(condition=g.get("condition", "open_sky"),
                                 street_azimuth=g.get("street_azimuth", 0.0))
        c = cfg.get("cellular", {}) or {}
        towers = [Tower(**t) for t in c.get("towers", [])] or None
        env.cellular = CellularWorld(towers=towers)
        b = cfg.get("ble", {}) or {}
        env.ble = BleWorld(interference=b.get("interference", 0.0))
        self.env = env

    def _load_firmware(self) -> None:
        fw_cfg = self.scenario.raw.get("firmware", {}) or {}
        for ref, spec in fw_cfg.items():
            mcu = self.twin.components.get(ref)
            if mcu is None:
                raise ValueError(f"firmware target {ref!r} not on board")
            if "behavioral" in spec:
                mod_name, fn_name = spec["behavioral"].split(":")
                # firmware modules may live next to the scenario or one level up
                roots = [str(self.scenario.dir), str(self.scenario.dir.parent)]
                sys.path[:0] = roots
                try:
                    fn = getattr(importlib.import_module(mod_name), fn_name)
                finally:
                    del sys.path[:len(roots)]
                mcu.load_behavioral(fn)
            elif "file" in spec:
                # stashed in params: the MCU loads it during start()
                mcu.params["slice_us"] = spec.get("slice_us", mcu.params.get("slice_us", 1000))
                mcu.params["firmware"] = str(self.scenario.resolve(spec["file"]))
            elif "c" in spec:
                # compile C source on the fly (zig toolchain, pip-installable)
                from ..cpu import cbuild
                profile = mcu.params.get("profile", "twin")
                code = self.scenario.resolve(spec["c"]).read_text()
                mcu.params["firmware"] = cbuild.compile_c(code, profile=profile)

    # -- stimuli ----------------------------------------------------------
    def _schedule_events(self) -> None:
        kernel = self.twin.kernel
        for ev in self.scenario.events:
            kernel.schedule_at(parse_time(ev["at"]), self._fire, ev)

    def _fire(self, ev: dict) -> None:
        do = ev["do"]
        kernel = self.twin.kernel
        kernel.trace.record(kernel.now, "event", do, ev)
        if do == "press":
            self.twin.comp(ev["target"]).press()
        elif do == "release":
            self.twin.comp(ev["target"]).release()
        elif do == "set_gnss_condition":
            self.env.gnss.condition = ev["value"]
        elif do == "set_cell_load":
            self.env.cellular.set_load(ev.get("tower", self.env.cellular.towers[0].id),
                                       float(ev["value"]))
        elif do == "set_ble_interference":
            self.env.ble.interference = float(ev["value"])
        elif do == "ble_connect":
            self.twin.comp(ev["target"]).central_connect()
        elif do == "ble_send":
            self.twin.comp(ev["target"]).central_send(ev["data"].encode())
        elif do == "uart_send":
            self._host_uart(ev["net"]).send(
                ev["data"].encode().decode("unicode_escape").encode())
        elif do == "set_net":
            net = self.twin.net(ev["net"])
            val = ev["value"]
            drive = {"low": Drive.low(), "high": Drive.high(ev.get("volts", 3.3)),
                     "release": Drive.release()}[val]
            net.drive("__scenario__", drive)
        elif do == "set_ambient":
            self.twin.power.ambient_c = float(ev["value"])
        elif do == "set_position":
            self.env.position = Position(ev["lat"], ev["lon"], ev.get("alt_m", 0.0))
        else:
            raise ValueError(f"unknown event {do!r}")

    def _host_uart(self, net_name: str):
        from ..comm import Uart
        ports = getattr(self, "_host_ports", {})
        if net_name not in ports:
            ports[net_name] = Uart(self.twin.kernel, "scenario",
                                   tx_net=self.twin.net(net_name), rx_net=None,
                                   baud=115200)
            self._host_ports = ports
        return ports[net_name]

    # -- run --------------------------------------------------------------
    def run(self) -> dict:
        sc = self.scenario
        board = self._load_board()
        cfg = sc.raw.get("board", {})
        self.twin = build_twin(board, seed=sc.seed,
                               ambient_c=(sc.raw.get("environment", {}) or {}).get("ambient_c", 25.0),
                               external_supplies=cfg.get("external_supplies"))
        self._setup_environment()
        self._load_firmware()
        self._schedule_events()
        self.twin.start()
        self.twin.kernel.run_until(sc.duration_ns)

        power_report = self.twin.power.report()
        self.results = evaluate(sc.assertions, self.twin, power_report)
        passed = all(r["passed"] for r in self.results)
        return {
            "scenario": sc.name,
            "passed": passed,
            "duration": sc.raw.get("duration", "10s"),
            "seed": sc.seed,
            "assertions": self.results,
            "power": power_report,
            "build_warnings": self.twin.warnings,
            "bind_report": self.bind_report,
            "lock": make_lock(sc, board),
        }


def run_scenario(path: str) -> dict:
    return ScenarioRun(load_scenario(path)).run()
