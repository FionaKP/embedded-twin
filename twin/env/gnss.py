"""GNSS world: satellite constellation, visibility geometry, signal model.

The default constellation is synthetic-but-physically-valid GPS: 24 SVs in
6 planes at 55° inclination, 26 560 km semi-major axis, built directly with
SGP4 orbital elements — fully offline and deterministic. Real TLE files
(e.g. from celestrak) load with ``GnssWorld.from_tle`` for live-sky work.

Signal model: elevation-dependent C/N0 with per-condition degradation:
  open_sky     — no extra loss
  urban_canyon — sats below `canyon_mask_el` blocked unless within
                 ±`street_half_width_deg` of the street axis; others -12 dB
  indoor       — all sats -25 dB
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from sgp4.api import Satrec, WGS72, jday

from .world import Position

MU = 398600.4418  # km^3/s^2


@dataclass
class SatSignal:
    prn: int
    azimuth: float
    elevation: float
    cn0: float          # dB-Hz; <=0 means blocked/invisible
    usable: bool


def synthetic_gps(planes: int = 6, per_plane: int = 4) -> list[Satrec]:
    sats = []
    a = 26559.7  # km
    no_rad_min = math.sqrt(MU / a ** 3) * 60.0
    for p in range(planes):
        for s in range(per_plane):
            sat = Satrec()
            sat.sgp4init(
                WGS72, "i",
                p * per_plane + s + 1,
                26800.5,               # epoch: days since 1949-12-31 (~2023)
                0.0, 0.0, 0.0,          # bstar, ndot, nddot
                0.01,                   # eccentricity
                math.radians(45.0),     # argument of perigee
                math.radians(55.0),     # inclination
                math.radians(s * 360.0 / per_plane + p * 15.0),  # mean anomaly
                no_rad_min,
                math.radians(p * 360.0 / planes),  # RAAN
            )
            sats.append(sat)
    return sats


def _gmst_rad(jd: float, fr: float) -> float:
    t = (jd - 2451545.0 + fr) / 36525.0
    gmst_sec = (67310.54841 + (876600.0 * 3600 + 8640184.812866) * t
                + 0.093104 * t * t - 6.2e-6 * t ** 3)
    return math.radians((gmst_sec % 86400.0) / 240.0)


def _geodetic_to_ecef(pos: Position) -> tuple[float, float, float]:
    a = 6378.137
    e2 = 6.69437999014e-3
    lat, lon = math.radians(pos.lat), math.radians(pos.lon)
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    h = pos.alt_m / 1000.0
    x = (n + h) * math.cos(lat) * math.cos(lon)
    y = (n + h) * math.cos(lat) * math.sin(lon)
    z = (n * (1 - e2) + h) * math.sin(lat)
    return x, y, z


def az_el(sat: Satrec, when: datetime, obs: Position) -> tuple[float, float]:
    jd, fr = jday(when.year, when.month, when.day,
                  when.hour, when.minute, when.second + when.microsecond / 1e6)
    err, r_teme, _v = sat.sgp4(jd, fr)
    if err != 0:
        return 0.0, -90.0
    # TEME -> ECEF (rotate by GMST)
    g = _gmst_rad(jd, fr)
    x = r_teme[0] * math.cos(g) + r_teme[1] * math.sin(g)
    y = -r_teme[0] * math.sin(g) + r_teme[1] * math.cos(g)
    z = r_teme[2]
    ox, oy, oz = _geodetic_to_ecef(obs)
    dx, dy, dz = x - ox, y - oy, z - oz
    lat, lon = math.radians(obs.lat), math.radians(obs.lon)
    # ECEF vector -> ENU
    e = -math.sin(lon) * dx + math.cos(lon) * dy
    n = (-math.sin(lat) * math.cos(lon) * dx
         - math.sin(lat) * math.sin(lon) * dy + math.cos(lat) * dz)
    u = (math.cos(lat) * math.cos(lon) * dx
         + math.cos(lat) * math.sin(lon) * dy + math.sin(lat) * dz)
    az = math.degrees(math.atan2(e, n)) % 360.0
    el = math.degrees(math.atan2(u, math.hypot(e, n)))
    return az, el


class GnssWorld:
    def __init__(self, sats: list[Satrec] | None = None,
                 condition: str = "open_sky",
                 street_azimuth: float = 0.0,
                 street_half_width_deg: float = 25.0,
                 canyon_mask_el: float = 40.0,
                 usable_cn0: float = 30.0):
        self.sats = sats if sats is not None else synthetic_gps()
        self.condition = condition
        self.street_azimuth = street_azimuth
        self.street_half_width_deg = street_half_width_deg
        self.canyon_mask_el = canyon_mask_el
        self.usable_cn0 = usable_cn0

    @staticmethod
    def from_tle(path: str, **kw) -> "GnssWorld":
        from sgp4.api import Satrec
        sats = []
        lines = [ln.strip() for ln in open(path) if ln.strip()]
        i = 0
        while i < len(lines) - 1:
            if lines[i].startswith("1 ") and lines[i + 1].startswith("2 "):
                sats.append(Satrec.twoline2rv(lines[i], lines[i + 1]))
                i += 2
            else:
                i += 1
        return GnssWorld(sats=sats, **kw)

    def sky_view(self, when: datetime, obs: Position) -> list[SatSignal]:
        out = []
        for idx, sat in enumerate(self.sats):
            az, el = az_el(sat, when, obs)
            if el < 5.0:
                continue
            cn0 = 38.0 + 9.0 * math.sin(math.radians(max(el, 1.0)))
            if self.condition == "urban_canyon":
                street_delta = min((az - self.street_azimuth) % 360,
                                   (self.street_azimuth - az) % 360)
                along_street = street_delta < self.street_half_width_deg or \
                    abs(street_delta - 180) < self.street_half_width_deg
                if el < self.canyon_mask_el and not along_street:
                    continue  # blocked by buildings
                if el < 70:
                    cn0 -= 12.0
            elif self.condition == "indoor":
                cn0 -= 25.0
            out.append(SatSignal(prn=idx + 1, azimuth=az, elevation=el,
                                 cn0=cn0, usable=cn0 >= self.usable_cn0))
        return out

    def usable_count(self, when: datetime, obs: Position) -> int:
        return sum(1 for s in self.sky_view(when, obs) if s.usable)
