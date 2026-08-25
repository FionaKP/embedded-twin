"""Behavioral firmware for the asset tracker.

Report cycle (every 60 s): wake, power the GNSS receiver, read temperature,
wait for fix + network, send a position report over the cellular modem,
blink the LED on success, power the GNSS down, sleep.

UART0 = GNSS (NMEA in), UART1 = modem (AT). I2C0 = TMP102.
"""
from twin.comm import LineAssembler

REPORT_PERIOD_US = 60_000_000   # 60 s
SEND_RETRY_LIMIT = 55           # keep trying for up to ~55 s per cycle


def main(api):
    st = {"fix": None, "temp": None, "registered": False, "sends": 0}
    gps, mdm = api.uart(0), api.uart(1)

    # -- GNSS NMEA parsing ------------------------------------------------
    def on_gps_line(line):
        if line.startswith("$GPGGA"):
            f = line.split(",")
            st["fix"] = (f[2], f[3], f[4], f[5]) if len(f) > 6 and f[6] == "1" else None
    gps_lines = LineAssembler(on_gps_line)
    gps.on_byte(gps_lines.feed)

    # -- modem URC/response handling --------------------------------------
    def on_mdm_line(line):
        if "+CREG: 1" in line:
            st["registered"] = True
        elif "+CREG: 0" in line:
            st["registered"] = False
        elif "SEND OK" in line:
            st["sends"] += 1
            api.gpio_write("PA5", 1)                      # success blink
            api.after(200_000, lambda: api.gpio_write("PA5", 0))
    mdm_lines = LineAssembler(on_mdm_line, terminator=b"\r\n")
    mdm.on_byte(mdm_lines.feed)

    api.after(3_000_000, lambda: mdm.send(b"ATE0\r"))     # quiet the echo

    # -- report cycle -----------------------------------------------------
    def report_cycle():
        api.wake()
        api.gpio_write("PB0", 1)                          # GNSS on
        api.i2c_write(0, 0x48, b"\x00")
        data = api.i2c_read(0, 0x48, 2)
        if data:
            st["temp"] = (int.from_bytes(data, "big", signed=True) >> 4) / 16
        try_send(0)

    def try_send(attempt):
        if st["fix"] and st["registered"]:
            lat, ns, lon, ew = st["fix"]
            payload = f"POS,{lat}{ns},{lon}{ew},T={st['temp']}"
            mdm.send(f'AT+CSOSEND=0,{len(payload)},"{payload}"\r'.encode())
            api.log(f"report sent ({payload})")
            api.after(2_000_000, finish_cycle)
        elif attempt < SEND_RETRY_LIMIT:
            api.after(1_000_000, lambda: try_send(attempt + 1))
        else:
            api.log("report skipped (no fix or no network)")
            finish_cycle()

    def finish_cycle():
        api.gpio_write("PB0", 0)                          # GNSS off
        api.sleep()

    api.every(REPORT_PERIOD_US, report_cycle)
    report_cycle()
