"""Radio component models: GNSS receiver, cellular modem, BLE module.

Each binds board-side behavior (UART protocol, power states) to the
environment worlds (kernel.env.gnss / .cellular / .ble). All timing noise
draws from the kernel's seeded RNG — runs are reproducible.
"""
from __future__ import annotations

import math
from typing import Optional

from ..comm import Uart, LineAssembler
from ..core.kernel import MS, SEC
from .base import Component, register


def _env(kernel):
    return getattr(kernel, "env", None)


class _PoweredRadio(Component):
    """Shared VCC-gating: models power off when their supply rail drops."""

    def _setup_supply(self) -> None:
        self.vcc = self.net("VCC", "VDD", "3V3", "VBAT")
        self.rail = self.vcc.name if self.vcc is not None else ""
        if self.vcc is not None:
            self.vcc.listen(lambda _n: self._supply_changed())

    def powered(self) -> bool:
        return self.vcc is None or self.vcc.is_high

    def _supply_changed(self) -> None:
        pass


@register("gnss.generic_nmea")
class GnssReceiver(_PoweredRadio):
    """u-blox-style NMEA GNSS receiver.

    params: baud (9600), start_mode (cold|warm|hot), fix_rate_hz (1),
    i_acq_ma (25), i_track_ma (20)
    TTFF model: needs >=4 usable satellites continuously; cold start also
    pays an ephemeris-download time that restarts if the sky drops below 4.
    """

    TTFF_S = {"cold": 28.0, "warm": 5.0, "hot": 1.0}

    def start(self) -> None:
        self._setup_supply()
        self.uart = Uart(self.kernel, self.ref,
                         tx_net=self.net("TX", "TXD"), rx_net=self.net("RX", "RXD"),
                         baud=int(self.params.get("baud", 9600)))
        self.start_mode = self.params.get("start_mode", "cold")
        self.i_acq = self.params.get("i_acq_ma", 25) / 1000
        self.i_track = self.params.get("i_track_ma", 20) / 1000
        self.has_fix = False
        self.lock_s = 0.0
        self.set_state("off")
        self.kernel.schedule(0, self._tick)

    def _tick(self) -> None:
        self.kernel.schedule(1 * SEC, self._tick)
        env = _env(self.kernel)
        if not self.powered() or env is None or env.gnss is None:
            if self.state != "off":
                self.set_state("off")
                self.set_load(self.rail, 0.0)
                self.has_fix, self.lock_s = False, 0.0
            return

        when, pos = env.utc_now(), env.position_now()
        sky = env.gnss.sky_view(when, pos)
        usable = [s for s in sky if s.usable]

        if len(usable) >= 4:
            self.lock_s += 1.0
        else:
            self.lock_s = 0.0
            if self.has_fix:
                self.has_fix = False
                self.log("fix lost")

        if not self.has_fix and self.lock_s >= self.TTFF_S[self.start_mode]:
            self.has_fix = True
            self.start_mode = "hot"  # ephemeris retained hereafter
            self.log(f"fix acquired ({len(usable)} SVs)")

        if self.has_fix != (self.state == "tracking"):
            self.set_state("tracking" if self.has_fix else "acquiring")
        elif self.state == "off":
            self.set_state("acquiring")
        self.set_load(self.rail, self.i_track if self.has_fix else self.i_acq)

        self._emit_nmea(sky, usable, pos)

    # -- NMEA -------------------------------------------------------------
    def _emit_nmea(self, sky, usable, pos) -> None:
        env = _env(self.kernel)
        t = env.utc_now()
        hhmmss = f"{t.hour:02d}{t.minute:02d}{t.second:02d}.00"
        if self.has_fix:
            hdop = round(0.9 + 4.0 / max(1, len(usable) - 2), 1)
            err_m = hdop * 2.5
            rng = self.kernel.rng
            dlat = rng.gauss(0, err_m) / 111_320
            dlon = rng.gauss(0, err_m) / (111_320 * max(0.01, math.cos(math.radians(pos.lat))))
            lat, lon = pos.lat + dlat, pos.lon + dlon
            gga = (f"GPGGA,{hhmmss},{self._fmt_lat(lat)},{self._fmt_lon(lon)},1,"
                   f"{len(usable):02d},{hdop},{pos.alt_m:.1f},M,0.0,M,,")
            date = f"{t.day:02d}{t.month:02d}{t.year % 100:02d}"
            rmc = (f"GPRMC,{hhmmss},A,{self._fmt_lat(lat)},{self._fmt_lon(lon)},"
                   f"0.0,0.0,{date},,,A")
        else:
            gga = f"GPGGA,{hhmmss},,,,,0,00,99.9,,M,,M,,"
            rmc = f"GPRMC,{hhmmss},V,,,,,,,,,,N"
        for body in (gga, rmc):
            self.uart.send(self._frame(body))

    @staticmethod
    def _frame(body: str) -> bytes:
        ck = 0
        for c in body:
            ck ^= ord(c)
        return f"${body}*{ck:02X}\r\n".encode()

    @staticmethod
    def _fmt_lat(lat: float) -> str:
        h = "N" if lat >= 0 else "S"
        lat = abs(lat)
        return f"{int(lat):02d}{(lat - int(lat)) * 60:07.4f},{h}"

    @staticmethod
    def _fmt_lon(lon: float) -> str:
        h = "E" if lon >= 0 else "W"
        lon = abs(lon)
        return f"{int(lon):03d}{(lon - int(lon)) * 60:07.4f},{h}"


@register("cellular.at_modem")
class CellModem(_PoweredRadio):
    """AT-command cellular modem with registration lifecycle.

    States: off -> booting -> searching -> registered (URCs on change).
    Network load above the tower's kick threshold detaches us mid-session.
    params: baud (115200), boot_s (2), i_idle_ma (8), i_search_ma (60),
    i_tx_ma (200)
    """

    def start(self) -> None:
        self._setup_supply()
        self.uart = Uart(self.kernel, self.ref,
                         tx_net=self.net("TXD", "TX"), rx_net=self.net("RXD", "RX"),
                         baud=int(self.params.get("baud", 115200)))
        self.lines = LineAssembler(self._on_cmd, terminator=b"\r")
        self.uart.on_byte(self._on_byte)
        self.registered = False
        self.echo = True
        self.set_state("off")
        env = _env(self.kernel)
        if env is not None and env.cellular is not None:
            env.cellular.on_load_change(self._load_changed)
        self.kernel.schedule(0, self._boot_check)

    # -- lifecycle --------------------------------------------------------
    def _boot_check(self) -> None:
        if self.powered() and self.state == "off":
            self.set_state("booting")
            self.kernel.schedule(int(self.params.get("boot_s", 2.0) * SEC),
                                 self._start_search)
        elif not self.powered() and self.state != "off":
            self.set_state("off")
            self.registered = False
            self.set_load(self.rail, 0.0)
        self.kernel.schedule(100 * MS, self._boot_check)

    def _start_search(self) -> None:
        if not self.powered():
            return
        self.set_state("searching")
        self.set_load(self.rail, self.params.get("i_search_ma", 60) / 1000)
        delay = self.kernel.rng.uniform(3.0, 8.0)
        self.kernel.schedule(int(delay * SEC), self._try_register)

    def _try_register(self) -> None:
        if not self.powered() or self.state != "searching":
            return
        env = _env(self.kernel)
        if env is None or env.cellular is None:
            self.kernel.schedule(10 * SEC, self._try_register)
            return
        best = env.cellular.best_tower(env.position_now())
        if best is None:
            self.kernel.schedule(10 * SEC, self._try_register)
            return
        tower, rssi = best
        if rssi < -110:
            self.log(f"no coverage (best {rssi:.0f} dBm)")
            self.kernel.schedule(10 * SEC, self._try_register)
            return
        if tower.load > tower.reject_threshold:
            self.log(f"attach rejected by {tower.id} (load {tower.load:.2f})")
            self.kernel.schedule(10 * SEC, self._try_register)
            return
        self.registered = True
        self.tower = tower
        self.set_state("registered")
        self.set_load(self.rail, self.params.get("i_idle_ma", 8) / 1000)
        self._urc("+CREG: 1")

    def _load_changed(self, tower) -> None:
        if self.registered and tower is getattr(self, "tower", None) \
                and tower.load > tower.kick_threshold:
            self.registered = False
            self.log(f"kicked off {tower.id} (load {tower.load:.2f})")
            self._urc("+CREG: 0")
            self._start_search()

    # -- AT interface -----------------------------------------------------
    def _on_byte(self, b: int) -> None:
        if self.state == "off":
            return
        if self.echo:
            self.uart.send(bytes([b]))
        self.lines.feed(b)

    def _urc(self, text: str) -> None:
        self.uart.send(f"\r\n{text}\r\n".encode())

    def _reply(self, *lines: str) -> None:
        payload = "".join(f"\r\n{ln}" for ln in lines) + "\r\n"
        self.uart.send(payload.encode())

    def _on_cmd(self, line: str) -> None:
        cmd = line.strip().upper()
        if not cmd.startswith("AT"):
            return
        env = _env(self.kernel)
        if cmd == "AT":
            self._reply("OK")
        elif cmd in ("ATE0", "ATE1"):
            self.echo = cmd.endswith("1")
            self._reply("OK")
        elif cmd == "AT+CSQ":
            if self.registered and env and env.cellular:
                rssi = env.cellular.rssi_dbm(self.tower, env.position_now())
                self._reply(f"+CSQ: {env.cellular.csq(rssi)},99", "OK")
            else:
                self._reply("+CSQ: 99,99", "OK")
        elif cmd == "AT+CREG?":
            self._reply(f"+CREG: 0,{1 if self.registered else 2}", "OK")
        elif cmd.startswith("AT+CSOSEND"):
            # simplified socket send: burst current, latency, OK/ERROR
            if self.registered:
                self.set_load(self.rail, self.params.get("i_tx_ma", 200) / 1000)
                self.kernel.schedule(int(0.5 * SEC), self._send_done)
            else:
                self._reply("ERROR")
        else:
            self._reply("OK")  # tolerate config commands

    def _send_done(self) -> None:
        if self.registered:
            self.set_load(self.rail, self.params.get("i_idle_ma", 8) / 1000)
            self._reply("SEND OK")
        else:
            self._reply("ERROR")


@register("ble.module")
class BleModule(_PoweredRadio):
    """BLE peripheral module with transparent UART bridge.

    Lifecycle: advertising -> connected (central connects via scenario) ->
    supervision-timeout drop under interference -> re-advertise.
    params: conn_interval_ms (50), supervision_timeout_ms (2000),
    i_adv_ma (1.0), i_conn_ma (0.5)
    """

    def start(self) -> None:
        self._setup_supply()
        self.uart = Uart(self.kernel, self.ref,
                         tx_net=self.net("TX", "TXD"), rx_net=self.net("RX", "RXD"),
                         baud=int(self.params.get("baud", 115200)))
        self.uart.on_byte(self._uplink_byte)
        self.conn_interval = int(self.params.get("conn_interval_ms", 50)) * MS
        self.timeout_ns = int(self.params.get("supervision_timeout_ms", 2000)) * MS
        self.connected = False
        self.peer_rx: list[int] = []   # bytes that reached the central
        self._missed_ns = 0
        self.set_state("advertising")
        self.set_load(self.rail, self.params.get("i_adv_ma", 1.0) / 1000)

    def central_connect(self) -> None:
        if self.state != "advertising" or not self.powered():
            return
        self.connected = True
        self._missed_ns = 0
        self.set_state("connected")
        self.set_load(self.rail, self.params.get("i_conn_ma", 0.5) / 1000)
        self.kernel.schedule(self.conn_interval, self._conn_event)

    def _conn_event(self) -> None:
        if not self.connected or not self.powered():
            return
        env = _env(self.kernel)
        p_loss = env.ble.packet_loss_probability() if env and env.ble else 0.0
        if self.kernel.rng.random() < p_loss:
            self._missed_ns += self.conn_interval
            if self._missed_ns >= self.timeout_ns:
                self._drop("supervision timeout")
                return
        else:
            self._missed_ns = 0
        self.kernel.schedule(self.conn_interval, self._conn_event)

    def _drop(self, reason: str) -> None:
        self.connected = False
        self.log(f"disconnected: {reason}")
        self.set_state("advertising")
        self.set_load(self.rail, self.params.get("i_adv_ma", 1.0) / 1000)

    def _uplink_byte(self, b: int) -> None:
        if self.connected:
            self.peer_rx.append(b)
            self.kernel.trace.record(self.kernel.now, "ble", self.ref, b)

    def central_send(self, data: bytes) -> None:
        """Data from the connected central appears on the module's UART TX."""
        if self.connected:
            self.uart.send(data)
