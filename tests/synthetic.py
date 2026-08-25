"""Synthetic fields so the whole pipeline can run without network access.

Used by `python scripts/update.py --offline` as a CI smoke test and for
checking units / mass conservation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import xarray as xr

from sargassum.drift import VectorField


# (lat, lon, peak fractional coverage) - realistic mats are a few percent of a
# pixel at their core, not tens of percent.
MATS = ((18.4, -64.2, 0.030), (17.6, -65.4, 0.022), (18.9, -63.0, 0.040),
        (17.2, -63.6, 0.018), (18.1, -66.9, 0.012))


def _afai_grid(dom, step, seed=1, mats=MATS):
    lat = np.arange(dom["lat_min"], dom["lat_max"], step)
    lon = np.arange(dom["lon_min"], dom["lon_max"], step)
    rng = np.random.default_rng(seed)
    afai = rng.normal(0.0, 3e-5, (lat.size, lon.size))
    LON, LAT = np.meshgrid(lon, lat)
    for mlat, mlon, cov in mats:
        d2 = ((LAT - mlat) / 0.16) ** 2 + ((LON - mlon) / 0.42) ** 2
        afai += cov * 0.0441 * np.exp(-d2)
    return lat, lon, afai


def synthetic_afai(cfg, domain="seed") -> xr.Dataset:
    dom = cfg["domains"][domain]
    step = 0.015 * int(dom.get("stride", 1))
    lat, lon, afai = _afai_grid(dom, step)
    t = pd.Timestamp.utcnow().floor("D")
    return xr.Dataset(
        {"AFAI": (("time", "latitude", "longitude"), afai[None, :, :])},
        coords={"time": [t], "latitude": lat, "longitude": lon},
    )


def synthetic_currents(cfg):
    dom = cfg["domains"]["seed"]
    lat = np.arange(dom["lat_min"], dom["lat_max"], 0.02)
    lon = np.arange(dom["lon_min"], dom["lon_max"], 0.02)
    LON, LAT = np.meshgrid(lon, lat)
    # westward Caribbean/Antilles current with a bit of structure
    u = -0.35 - 0.10 * np.sin((LAT - 14) * 1.2)
    v = -0.05 + 0.06 * np.cos((LON + 65) * 0.8)
    latest = VectorField(lat, lon, u, v)
    mean = VectorField(lat, lon, u * 0.9, v * 0.9)
    return latest, mean, pd.Timestamp.utcnow().floor("h")


def synthetic_wind(cfg, hours=120):
    dom = cfg["domains"]["seed"]
    lat = np.arange(dom["lat_min"], dom["lat_max"], 0.05)
    lon = np.arange(dom["lon_min"], dom["lon_max"], 0.05)
    shape = (lat.size, lon.size)
    now = pd.Timestamp.utcnow().floor("h")
    times = pd.date_range(now - pd.Timedelta(hours=3), periods=hours + 4,
                          freq="h", tz="UTC")
    fields = []
    for k in range(len(times)):
        # easterly trades, 6-9 m/s, weak diurnal modulation
        spd = 7.5 + 1.5 * np.sin(2 * np.pi * k / 24)
        fields.append(VectorField(lat, lon,
                                  np.full(shape, -spd * 0.97),
                                  np.full(shape, spd * 0.22)))
    return fields, times


def synthetic_bundle(cfg):
    latest, mean, t = synthetic_currents(cfg)
    hours = int(cfg.get_path("run.horizon_hours", 120))
    return {
        "afai_seed": synthetic_afai(cfg, "seed"),
        "afai_map": synthetic_afai(cfg, "map"),
        "currents": (latest, mean, t),
        "wind": synthetic_wind(cfg, hours),
        "afai_time": pd.Timestamp.utcnow().floor("D"),
    }
