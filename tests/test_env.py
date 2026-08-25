from datetime import datetime, timezone

from twin.build import build_twin
from twin.core.kernel import SEC
from twin.env import Environment, Position, GnssWorld, CellularWorld, Tower, BleWorld
from twin.ir import BoardIR, ComponentIR, NetIR, NetNode

T0 = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)
HERE = Position(42.2626, -71.8023)


def radio_board(model: str, extra_params: dict | None = None) -> BoardIR:
    b = BoardIR(name="radio")
    b.add_component(ComponentIR(ref="M1", model=model, params=extra_params or {},
                                pins={"1": "VCC", "2": "TX", "3": "RX"}))
    b.add_net(NetIR("+3V3", "power", [NetNode("M1", "1")]))
    b.add_net(NetIR("GPS_TX", "signal", [NetNode("M1", "2")]))
    b.add_net(NetIR("GPS_RX", "signal", [NetNode("M1", "3")]))
    return b


def make_twin(model: str, params: dict | None = None):
    twin = build_twin(radio_board(model, params), external_supplies={"+3V3": 3.3})
    env = Environment(twin.kernel, start_utc=T0, position=HERE)
    env.gnss = GnssWorld()
    env.cellular = CellularWorld()
    env.ble = BleWorld()
    twin.start()
    return twin, env


def nmea_lines(twin, net="GPS_TX"):
    return twin.kernel.trace.uart_bytes(net).decode(errors="replace").splitlines()


def test_gnss_cold_start_ttff_open_sky():
    twin, _env = make_twin("gnss.generic_nmea")
    twin.kernel.run_until(60 * SEC)
    gps = twin.comp("M1")
    assert gps.has_fix
    logs = [v for _t, n, v in twin.kernel.trace.logs() if n == "M1"]
    assert any("fix acquired" in v for v in logs)
    # TTFF ~28 s cold: no fix at 20 s, fix by 35 s
    fix_time = next(t for t, _n, v in twin.kernel.trace.logs() if "fix acquired" in str(v))
    assert 25 * SEC <= fix_time <= 35 * SEC


def test_gnss_nmea_stream_is_valid():
    twin, _env = make_twin("gnss.generic_nmea")
    twin.kernel.run_until(40 * SEC)
    lines = nmea_lines(twin)
    assert any(ln.startswith("$GPGGA") for ln in lines)
    assert any(ln.startswith("$GPRMC") for ln in lines)
    for ln in lines:
        if "*" not in ln or not ln.startswith("$"):
            continue
        body, ck = ln[1:].rsplit("*", 1)
        calc = 0
        for c in body:
            calc ^= ord(c)
        assert f"{calc:02X}" == ck.strip()
    # after fix: GGA quality flag 1 and plausible position
    fixed = [ln for ln in lines if ln.startswith("$GPGGA") and ",1," in ln]
    assert fixed
    assert "4215." in fixed[-1]  # 42.26° -> 42°15.7'


def test_gnss_indoor_never_fixes_and_urban_canyon_is_slow_or_lossy():
    twin, env = make_twin("gnss.generic_nmea")
    env.gnss.condition = "indoor"
    twin.kernel.run_until(120 * SEC)
    assert not twin.comp("M1").has_fix

    twin2, env2 = make_twin("gnss.generic_nmea")
    env2.gnss.condition = "urban_canyon"
    twin2.kernel.run_until(120 * SEC)
    # urban canyon at this time/place: 3 usable SVs -> no fix (needs 4)
    assert not twin2.comp("M1").has_fix


def test_gnss_fix_lost_when_walking_indoors():
    twin, env = make_twin("gnss.generic_nmea")
    twin.kernel.run_until(40 * SEC)
    assert twin.comp("M1").has_fix
    env.gnss.condition = "indoor"
    twin.kernel.run_until(45 * SEC)
    assert not twin.comp("M1").has_fix
    assert any("fix lost" in str(v) for _t, _n, v in twin.kernel.trace.logs())


def at_exchange(twin, cmd: bytes, run_s: float):
    """Send an AT command into the modem's RX and return everything it TXed."""
    from twin.comm import Uart
    host = getattr(twin, "_host_uart", None)
    if host is None:
        host = Uart(twin.kernel, "host", tx_net=twin.net("GPS_RX"),
                    rx_net=twin.net("GPS_TX"), baud=115200)
        twin._host_uart = host
    mark = twin.kernel.now
    host.send(cmd)
    twin.kernel.run_until(twin.kernel.now + int(run_s * SEC))
    return twin.kernel.trace.uart_bytes("GPS_TX", start=mark)


def test_modem_boots_registers_and_answers_at():
    twin, env = make_twin("cellular.at_modem", {"boot_s": 1.0})
    twin.kernel.run_until(15 * SEC)  # boot + search + register
    modem = twin.comp("M1")
    assert modem.registered
    urc = twin.kernel.trace.uart_bytes("GPS_TX")
    assert b"+CREG: 1" in urc

    out = at_exchange(twin, b"AT+CSQ\r", 1.0)
    assert b"+CSQ:" in out and b"OK" in out
    csq = int(out.split(b"+CSQ: ")[1].split(b",")[0])
    assert 5 <= csq <= 31  # ~1.2 km from tower -> decent signal

    out = at_exchange(twin, b"AT+CREG?\r", 1.0)
    assert b"+CREG: 0,1" in out


def test_modem_rejected_when_network_full_then_kicked():
    twin, env = make_twin("cellular.at_modem", {"boot_s": 1.0})
    env.cellular.towers[0].load = 0.95  # full network: attach rejected
    twin.kernel.run_until(20 * SEC)
    assert not twin.comp("M1").registered
    assert any("rejected" in str(v) for _t, _n, v in twin.kernel.trace.logs())

    env.cellular.set_load("cell-A", 0.2)  # network recovers
    twin.kernel.run_until(45 * SEC)
    assert twin.comp("M1").registered

    env.cellular.set_load("cell-A", 0.97)  # overload: kicked off
    twin.kernel.run_until(46 * SEC)
    assert not twin.comp("M1").registered
    assert any("kicked off" in str(v) for _t, _n, v in twin.kernel.trace.logs())
    assert b"+CREG: 0" in twin.kernel.trace.uart_bytes("GPS_TX")


def test_ble_connects_bridges_data_and_drops_under_interference():
    twin, env = make_twin("ble.module")
    ble = twin.comp("M1")
    assert ble.state == "advertising"
    ble.central_connect()
    twin.kernel.run_until(1 * SEC)
    assert ble.connected

    ble.central_send(b"ping")
    twin.kernel.run_until(2 * SEC)
    assert twin.kernel.trace.uart_bytes("GPS_TX").endswith(b"ping")

    env.ble.interference = 1.0  # jammed
    twin.kernel.run_until(10 * SEC)
    assert not ble.connected
    assert ble.state == "advertising"
    assert any("supervision timeout" in str(v) for _t, _n, v in twin.kernel.trace.logs())
