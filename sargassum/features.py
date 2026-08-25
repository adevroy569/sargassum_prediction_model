"""Feature construction shared by live prediction and historical backfill.

The learned model predicts in-situ Sargassum influx (kg per metre of
shoreline) at a receptor from satellite-derived offshore biomass in nested
upstream catchments at several weekly lags, plus seasonality.
"""
from __future__ import annotations

from typing import Dict, List, Sequence

import numpy as np
import pandas as pd

EARTH_R_KM = 6371.0


def _distance_km(lat_grid, lon_grid, lat0, lon0):
    dlat = np.radians(lat_grid - lat0)
    dlon = np.radians(lon_grid - lon0)
    a = (np.sin(dlat / 2) ** 2
         + np.cos(np.radians(lat0)) * np.cos(np.radians(lat_grid))
         * np.sin(dlon / 2) ** 2)
    return 2 * EARTH_R_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def catchment_indices(bio, lat0: float, lon0: float, cfg) -> Dict[str, float]:
    """Wet biomass (tonnes) inside nested radii around a receptor.

    Beyond `upstream_only_beyond_km` only the eastern (upstream) half is
    counted, because Sargassum reaches Puerto Rico on the westward North
    Equatorial Current / Caribbean Current and the easterly trades.
    """
    radii: Sequence[float] = cfg.get_path("model.catchment_radii_km",
                                          [100.0, 250.0, 600.0])
    up_beyond = float(cfg.get_path("model.upstream_only_beyond_km", 100.0))

    lat = np.asarray(bio["latitude"].values, dtype=float)
    lon = np.asarray(bio["longitude"].values, dtype=float)
    mass = np.asarray(bio["cell_mass_kg"].values, dtype=float)
    LON, LAT = np.meshgrid(lon, lat)
    dist = _distance_km(LAT, LON, lat0, lon0)
    upstream = LON >= (lon0 - 0.15)

    out: Dict[str, float] = {}
    for r in radii:
        sel = dist <= r
        if r > up_beyond:
            sel = sel & upstream
        out[f"biomass_t_r{int(r)}"] = float(np.nansum(mass[sel]) / 1000.0)
    out["biomass_t_domain"] = float(np.nansum(mass) / 1000.0)
    return out


def seasonal_terms(when: pd.Timestamp) -> Dict[str, float]:
    doy = when.dayofyear
    ang = 2 * np.pi * doy / 365.25
    return {"doy_sin": float(np.sin(ang)), "doy_cos": float(np.cos(ang)),
            "doy_sin2": float(np.sin(2 * ang)), "doy_cos2": float(np.cos(2 * ang))}


def assemble(rows: List[dict], lags_weeks: Sequence[int]) -> pd.DataFrame:
    """Turn per-(station, date) index rows into a lagged feature table."""
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.sort_values(["station", "time"]).reset_index(drop=True)
    index_cols = [c for c in df.columns if c.startswith("biomass_t_")]
    out = []
    for station, g in df.groupby("station", sort=False):
        g = g.set_index("time").sort_index()
        base = g[index_cols].copy()
        merged = base.copy()
        for lag in lags_weeks:
            if lag == 0:
                continue
            shifted = base.shift(freq=pd.Timedelta(weeks=lag))
            shifted = shifted.reindex(base.index, method="nearest",
                                      tolerance=pd.Timedelta(days=4))
            shifted.columns = [f"{c}_lag{lag}w" for c in index_cols]
            merged = merged.join(shifted)
        merged["station"] = station
        for col in g.columns:
            if col not in index_cols and col not in merged.columns:
                merged[col] = g[col]
        out.append(merged.reset_index())
    res = pd.concat(out, ignore_index=True)
    # growth/trend terms
    for c in index_cols:
        prev = f"{c}_lag1w"
        if prev in res.columns:
            res[f"{c}_trend"] = np.log1p(res[c]) - np.log1p(res[prev])
    seas = res["time"].apply(lambda t: pd.Series(seasonal_terms(t)))
    return pd.concat([res, seas], axis=1)


DROP_COLS = {"time", "biomass_kg_per_m", "latitude", "longitude",
             "source", "station", "flag"}


def feature_columns(df: pd.DataFrame) -> List[str]:
    """Numeric model inputs, in a stable order."""
    cols = [c for c in df.columns
            if c not in DROP_COLS and pd.api.types.is_numeric_dtype(df[c])]
    return sorted(cols)
