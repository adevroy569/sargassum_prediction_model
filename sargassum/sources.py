"""Fetch layer: one function per data product, all returning tidy objects."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import xarray as xr

from . import erddap
from .drift import VectorField

log = logging.getLogger(__name__)


# --------------------------------------------------------------------- AFAI
def fetch_afai(cfg, domain: str = "seed", product: str = "7d",
               time_sel: str = "(last)", cache_dir: Optional[Path] = None,
               cache_ttl_s: float = 3600.0) -> xr.Dataset:
    src = cfg["sources"]["afai"]
    dom = cfg["domains"][domain]
    ds_id = src[f"dataset_{product}"]
    return erddap.griddap(
        server=src["server"], dataset=ds_id, variables=[src["variable"]],
        time_sel=time_sel,
        lat=(dom["lat_min"], dom["lat_max"]),
        lon=(dom["lon_min"], dom["lon_max"]),
        stride=int(dom.get("stride", 1)),
        cache_dir=cache_dir, cache_ttl_s=cache_ttl_s,
    )


def afai_latest_time(cfg, product: str = "7d") -> pd.Timestamp:
    src = cfg["sources"]["afai"]
    return erddap.latest_time(src["server"], src[f"dataset_{product}"])


def choose_afai_product(cfg) -> str:
    """Pick the freshest useful AFAI composite.

    The 7-day composite has the fewest cloud gaps and is preferred, but the
    products do not always update in step, so fall back to 3-day / 1-day when
    the 7-day scene is stale.
    """
    src = cfg["sources"]["afai"]
    tol = pd.Timedelta(days=float(src.get("prefer_7d_within_days", 6)))
    latest = {}
    for prod in ("7d", "3d", "1d"):
        key = f"dataset_{prod}"
        if key not in src:
            continue
        try:
            latest[prod] = erddap.latest_time(src["server"], src[key])
        except Exception as exc:  # noqa: BLE001
            log.warning("AFAI %s unavailable: %s", prod, exc)
    if not latest:
        raise erddap.ErddapError("no AFAI product reachable")
    newest = max(latest.values())
    if "7d" in latest and (newest - latest["7d"]) <= tol:
        return "7d"
    best = max(latest, key=lambda k: latest[k])
    log.warning("7-day AFAI is stale; using %s (%s)", best, latest[best])
    return best


# ----------------------------------------------------------------- currents
def fetch_currents(cfg, cache_dir: Optional[Path] = None
                   ) -> Tuple[VectorField, VectorField, pd.Timestamp]:
    """Latest HF-radar field plus the recent-mean field used for persistence."""
    src = cfg["sources"]["currents"]
    dom = cfg["domains"]["seed"]
    window = int(src.get("mean_window_hours", 72))

    for ds_id in (src["dataset"], src.get("dataset_fallback")):
        if not ds_id:
            continue
        try:
            t_end = erddap.latest_time(src["server"], ds_id)
            t_start = t_end - pd.Timedelta(hours=window)
            sel = (f"({t_start.strftime('%Y-%m-%dT%H:%M:%SZ')}):"
                   f"({t_end.strftime('%Y-%m-%dT%H:%M:%SZ')})")
            ds = erddap.griddap(
                server=src["server"], dataset=ds_id,
                variables=[src["u"], src["v"]], time_sel=sel,
                lat=(dom["lat_min"], dom["lat_max"]),
                lon=(dom["lon_min"], dom["lon_max"]),
                cache_dir=cache_dir, cache_ttl_s=1800,
            )
            lat = np.asarray(ds["latitude"].values, dtype=float)
            lon = np.asarray(ds["longitude"].values, dtype=float)
            u = np.asarray(ds[src["u"]].values, dtype=float)
            v = np.asarray(ds[src["v"]].values, dtype=float)
            latest = VectorField(lat, lon, u[-1], v[-1])
            mean = VectorField(lat, lon,
                               np.nanmean(u, axis=0), np.nanmean(v, axis=0))
            log.info("HF radar %s: %s, %.0f%% valid cells", ds_id, t_end,
                     100 * np.isfinite(u[-1]).mean())
            return latest, mean, t_end
        except Exception as exc:  # noqa: BLE001 - fall through to next dataset
            log.warning("currents %s failed: %s", ds_id, exc)
    raise erddap.ErddapError("no HF-radar current field available")


# --------------------------------------------------------------------- wind
def _wind_candidates(src) -> List[str]:
    """Configured wind dataset first, then any declared fallbacks."""
    out = [src["dataset"]]
    fb = src.get("dataset_fallback")
    if isinstance(fb, str):
        out.append(fb)
    elif isinstance(fb, (list, tuple)):
        out.extend(fb)
    return [d for i, d in enumerate(out) if d and d not in out[:i]]


def _wind_report(server: str, dataset: str) -> str:
    """What the server actually offers, for the log when a fetch fails."""
    bits = [f"dataset={dataset}"]
    try:
        tb = erddap.griddap_time_bounds(server, dataset)
        bits.append(f"time={tb[0]} .. {tb[1]}" if tb else "time=unreadable")
    except Exception as exc:  # noqa: BLE001
        bits.append(f"time=error({exc})")
    try:
        b = erddap.griddap_bounds(server, dataset)
        bits.append(f"lat={b.get('latitude')} lon={b.get('longitude')}")
    except Exception as exc:  # noqa: BLE001
        bits.append(f"box=error({exc})")
    return "; ".join(bits)


def fetch_wind(cfg, hours_ahead: int, cache_dir: Optional[Path] = None
               ) -> Tuple[List[VectorField], pd.DatetimeIndex]:
    """WRF-NMM 10 m wind, now through `hours_ahead`, as hourly fields.

    The requested window is clamped to the dataset's own time axis. A 2 km WRF
    nest runs far shorter than the 120 hour drift horizon, and ERDDAP fails the
    entire request rather than truncating it, so an unclamped ask returned
    nothing at all and the windage term - the dominant beaching mechanism -
    was silently switched off for every run.
    """
    src = cfg["sources"]["wind"]
    dom = cfg["domains"]["seed"]
    now = erddap.as_utc(pd.Timestamp.utcnow()).floor("h")
    want0 = now - pd.Timedelta(hours=3)
    want1 = now + pd.Timedelta(hours=hours_ahead)

    errors = []
    for dataset in _wind_candidates(src):
        try:
            bounds = erddap.griddap_time_bounds(src["server"], dataset)
            t0, t1 = erddap.clamp_time(want0, want1, bounds)
            if t1 <= t0:
                raise erddap.ErddapError(
                    f"{dataset} holds no times in the requested window "
                    f"({want0} .. {want1}); dataset covers {bounds}")
            if bounds and t1 < want1:
                log.info("%s forecast ends %s, %.0f h short of the %d h "
                         "horizon; wind is held constant past that point",
                         dataset, t1, (want1 - t1).total_seconds() / 3600.0,
                         hours_ahead)
            ds = erddap.griddap(
                server=src["server"], dataset=dataset,
                variables=[src["u"], src["v"]],
                time_sel=f"({t0.strftime('%Y-%m-%dT%H:%M:%SZ')}):"
                         f"({t1.strftime('%Y-%m-%dT%H:%M:%SZ')})",
                lat=(dom["lat_min"], dom["lat_max"]),
                lon=(dom["lon_min"], dom["lon_max"]),
                cache_dir=cache_dir, cache_ttl_s=1800,
            )
            lat = np.asarray(ds["latitude"].values, dtype=float)
            lon = np.asarray(ds["longitude"].values, dtype=float)
            u = np.asarray(ds[src["u"]].values, dtype=float)
            v = np.asarray(ds[src["v"]].values, dtype=float)
            times = pd.to_datetime(ds["time"].values, utc=True)
            fields = [VectorField(lat, lon, np.nan_to_num(u[i]),
                                  np.nan_to_num(v[i]))
                      for i in range(u.shape[0])]
            log.info("wind %s: %d fields, %s .. %s", dataset, len(fields),
                     times[0], times[-1])
            return fields, times
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{dataset}: {type(exc).__name__}: {exc}")
            log.warning("wind dataset %s failed (%s); server offers: %s",
                        dataset, exc, _wind_report(src["server"], dataset))

    raise erddap.ErddapError("no wind dataset reachable -> " + " | ".join(errors))


def wind_at(fields: List[VectorField], times: pd.DatetimeIndex,
            when: pd.Timestamp) -> Optional[VectorField]:
    if not fields:
        return None
    i = int(np.argmin(np.abs((times - when).total_seconds())))
    return fields[i]


# ------------------------------------------------------------------- in situ
def fetch_insitu(cfg, since: Optional[str] = None,
                 cache_dir: Optional[Path] = None) -> pd.DataFrame:
    """Weekly Sargassum biomass (kg per m of shoreline) from all stations."""
    src = cfg["sources"]["insitu"]
    var = src["variable"]
    frames = []
    for ds_id in src["stations"]:
        cons = []
        if since:
            cons.append(f"time>={since}")
        try:
            df = erddap.tabledap(
                src["server"], ds_id,
                ["time", "latitude", "longitude", var],
                cons, cache_dir=cache_dir, cache_ttl_s=6 * 3600,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("in-situ %s failed: %s", ds_id, exc)
            continue
        if df.empty:
            continue
        df = df.rename(columns={var: "biomass_kg_per_m"})
        df["station"] = ds_id.replace("Sargassum_Biomass_", "")
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["time", "latitude", "longitude",
                                     "biomass_kg_per_m", "station"])
    out = pd.concat(frames, ignore_index=True)
    out = out.dropna(subset=["time", "biomass_kg_per_m"])
    return out.sort_values("time").reset_index(drop=True)


# --------------------------------------------------------------------- waves
def fetch_wave_height(cfg, cache_dir: Optional[Path] = None
                      ) -> Optional[xr.Dataset]:
    src = cfg["sources"].get("waves")
    if not src:
        return None
    dom = cfg["domains"]["map"]
    for var in ("hs", "Hsig", "significant_wave_height", "hsig"):
        try:
            return erddap.griddap(
                server=src["server"], dataset=src["dataset"],
                variables=[var], time_sel="(last)",
                lat=(dom["lat_min"], dom["lat_max"]),
                lon=(dom["lon_min"], dom["lon_max"]),
                cache_dir=cache_dir, cache_ttl_s=3600,
            )
        except Exception:  # noqa: BLE001 - try the next likely variable name
            continue
    log.warning("no usable wave variable found in %s", src["dataset"])
    return None
