from twin.core import SimKernel, Net, Level, Drive
from twin.core.kernel import MS, US


def test_events_dispatch_in_time_then_insertion_order():
    k = SimKernel()
    order = []
    k.schedule(20, order.append, "b")
    k.schedule(10, order.append, "a")
    k.schedule(20, order.append, "c")  # same time as b, scheduled later
    k.run()
    assert order == ["a", "b", "c"]
    assert k.now == 20


def test_cancel_and_run_until():
    k = SimKernel()
    fired = []
    ev = k.schedule(5, fired.append, "cancelled")
    k.schedule(10, fired.append, "kept")
    k.schedule(2 * MS, fired.append, "late")
    ev.cancel()
    k.run_until(1 * MS)
    assert fired == ["kept"]
    assert k.now == 1 * MS
    k.run_until(3 * MS)
    assert fired == ["kept", "late"]


def test_net_strength_resolution():
    k = SimKernel()
    net = Net(k, "SDA")
    net.drive("R1", Drive.pull_up(3.3))
    k.run()
    assert net.level == Level.HIGH and net.voltage == 3.3
    # open-drain device pulls low: STRONG beats PULL
    net.drive("U1.SDA", Drive.low())
    k.run()
    assert net.level == Level.LOW
    net.drive("U1.SDA", Drive.release())
    k.run()
    assert net.level == Level.HIGH  # pull-up restores


def test_net_contention_is_flagged():
    k = SimKernel()
    net = Net(k, "GPIO3")
    net.drive("U1", Drive.high(3.3))
    net.drive("U2", Drive.low())
    k.run()
    assert net.level == Level.X and net.contention
    conflicts = [e for e in k.trace.select(kind="net", name="GPIO3")
                 if e.value.get("contention")]
    assert conflicts


def test_listeners_fire_on_change_without_recursion():
    k = SimKernel()
    a, b = Net(k, "A"), Net(k, "B")
    # inverter: B = !A
    a.listen(lambda n: b.drive("inv", Drive.low() if n.is_high else Drive.high(3.3)))
    a.drive("src", Drive.high(3.3))
    k.run()
    assert b.level == Level.LOW
    a.drive("src", Drive.low())
    k.run()
    assert b.level == Level.HIGH


def test_trace_queries():
    k = SimKernel()
    net = Net(k, "CLK")
    for i in range(4):
        lvl = Drive.high(3.3) if i % 2 == 0 else Drive.low()
        k.schedule(i * 10 * US, net.drive, "gen", lvl)
    k.run()
    assert k.trace.net_level_at("CLK", 5 * US) == "1"
    assert k.trace.net_level_at("CLK", 15 * US) == "0"
    assert len(k.trace.transitions("CLK")) == 4


def test_determinism_same_seed_same_trace():
    def run():
        k = SimKernel(seed=42)
        net = Net(k, "N")
        for _ in range(50):
            delay = k.rng.randrange(1, 1000)
            lvl = Drive.high(3.3) if k.rng.random() < 0.5 else Drive.low()
            k.schedule(delay, net.drive, "gen", lvl)
        k.run()
        return [(e.time, e.value["level"]) for e in k.trace.select(kind="net")]
    assert run() == run()
