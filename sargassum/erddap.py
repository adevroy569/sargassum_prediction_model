"""Thin, dependency-light ERDDAP client with retries and on-disk caching.

Everything the pipeline needs from the network goes through here, so failure
handling, throttling and caching live in one place.
"""
from __future__ import annotations

import hashlib
import io
import logging
import time
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests
import xarray as xr

log = logging.getLogger(__name__)

USER_AGENT = "sargassum-pr-forecast/1.0 (+https://github.com/)"
TIMEOUT = 180
RETRIES = 4
BACKOFF = 5.0


class ErddapError(RuntimeError):
    pass


def _get(url: str, cache_dir: Optional[Path] = None,
         cache_ttl_s: float = 0.0) -> bytes:
    """GET with retries and optional short-lived disk cache."""
    cache_file = None
    if cache_dir is not None and cache_ttl_s > 0:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha1(url.encode()).hexdigest()[:20]
        cache_file = cache_dir / f"{key}.bin"
        if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < cache_ttl_s:
            log.debug("cache hit %s", url)
            return cache_file.read_bytes()

    last = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, timeout=TIMEOUT,
                             headers={"User-Agent": USER_AGENT})
            if r.status_code == 200:
                if cache_file is not None:
                    cache_file.write_bytes(r.content)
                return r.content
            # ERDDAP returns 404 with a text body when a subset is empty
            last = ErddapError(f"HTTP {r.status_code} for {url}\n"
                               f"{r.text[:400]}")
        except requests.RequestException as exc:  # network hiccup
            last = ErddapError(f"{type(exc).__name__}: {exc} for {url}")
        sleep = BACKOFF * (2 ** attempt)
        log.warning("ERDDAP attempt %d/%d failed (%s); retrying in %.0fs",
                    attempt + 1, RETRIES, last, sleep)
        time.sleep(sleep)
    raise last  # type: ignore[misc]


def _rng(lo, hi, stride: int = 1) -> str:
    if stride and stride > 1:
        return f"[({lo}):{stride}:({hi})]"
    return f"[({lo}):({hi})]"


def griddap(server: str, dataset: str, variables: Sequence[str],
            time_sel: str, lat: tuple, lon: tuple, stride: int = 1,
            cache_dir: Optional[Path] = None,
            cache_ttl_s: float = 0.0,
            extra_dims: str = "", auto_clamp: bool = True) -> xr.Dataset:
    """Download a griddap subset as NetCDF and open it with xarray.

    `time_sel` is an ERDDAP time selector, e.g. "(last)" or
    "(2026-08-01T00:00:00Z):(2026-08-05T00:00:00Z)".
    """
    if auto_clamp:
        b = griddap_bounds(server, dataset)
        lat = clamp(lat, b.get("latitude"))
        lon = clamp(lon, b.get("longitude"))
        if lat[0] >= lat[1] or lon[0] >= lon[1]:
            raise ErddapError(
                f"{dataset} does not overlap the requested box "
                f"(lat {lat}, lon {lon})")
    parts = []
    for v in variables:
        parts.append(
            f"{v}[{time_sel}]{extra_dims}"
            f"{_rng(lat[0], lat[1], stride)}{_rng(lon[0], lon[1], stride)}"
        )
    query = quote(",".join(parts), safe="[]():,.-+*")
    url = f"{server}/griddap/{dataset}.nc?{query}"
    log.info("griddap %s %s", dataset, time_sel)
    raw = _get(url, cache_dir, cache_ttl_s)
    return xr.open_dataset(io.BytesIO(raw))


def griddap_axis(server: str, dataset: str, axis: str = "time",
                 selector: str = "last",
                 cache_dir: Optional[Path] = None,
                 cache_ttl_s: float = 0.0) -> np.ndarray:
    """Fetch a single coordinate axis value (latest time, grid corner, ...)."""
    url = f"{server}/griddap/{dataset}.csv?{axis}%5B{selector}%5D"
    txt = _get(url, cache_dir, cache_ttl_s).decode("utf-8", "replace")
    rows = [r for r in txt.splitlines() if r.strip()]
    vals = [r.split(",")[0] for r in rows[2:]]
    if axis == "time":
        return pd.to_datetime(vals, utc=True).values
    return np.array([float(v) for v in vals])


_BOUNDS: dict = {}


def griddap_bounds(server: str, dataset: str) -> dict:
    """Axis extents of a griddap dataset, so requests can be clamped.

    ERDDAP rejects a subset whose corner falls outside the grid, and the
    products used here have very different footprints (the WRF wind grid is a
    small box inside the much larger AFAI grid), so every request is clamped to
    what the dataset actually covers.
    """
    key = (server, dataset)
    if key in _BOUNDS:
        return _BOUNDS[key]
    out = {}
    for axis in ("latitude", "longitude"):
        vals = []
        for sel in ("0", "last"):
            try:
                vals.append(float(griddap_axis(server, dataset, axis, sel)[0]))
            except Exception as exc:  # noqa: BLE001
                log.warning("could not read %s %s: %s", dataset, axis, exc)
                vals = []
                break
        if vals:
            out[axis] = (min(vals), max(vals))
    _BOUNDS[key] = out
    return out


_TIME_BOUNDS: dict = {}


def griddap_time_bounds(server: str, dataset: str
                        ) -> Optional[Tuple[pd.Timestamp, pd.Timestamp]]:
    """First and last time step a griddap dataset actually holds.

    ERDDAP rejects a value-based subset whose corner falls outside an axis, and
    the time axis is no exception: asking a 84-hour wind forecast for a step
    120 hours out fails the *whole* request rather than returning what exists.
    `griddap_bounds` only ever clamped latitude and longitude, so forecast
    products were being asked for times past the end of their own run.
    """
    key = (server, dataset)
    if key in _TIME_BOUNDS:
        return _TIME_BOUNDS[key]
    out = None
    try:
        first = pd.Timestamp(griddap_axis(server, dataset, "time", "0")[0])
        last = pd.Timestamp(griddap_axis(server, dataset, "time", "last")[-1])
        out = (first, last)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read %s time axis: %s", dataset, exc)
    _TIME_BOUNDS[key] = out
    return out


def clamp_time(t0: pd.Timestamp, t1: pd.Timestamp,
               bounds: Optional[Tuple[pd.Timestamp, pd.Timestamp]]
               ) -> Tuple[pd.Timestamp, pd.Timestamp]:
    """Pull a requested window inside what the dataset covers."""
    if not bounds:
        return t0, t1
    lo, hi = bounds
    return max(t0, lo), min(t1, hi)


def clamp(rng_req: tuple, bounds: Optional[tuple]) -> tuple:
    if not bounds:
        return rng_req
    lo, hi = min(rng_req), max(rng_req)
    return (max(lo, bounds[0]), min(hi, bounds[1]))


def latest_time(server: str, dataset: str) -> pd.Timestamp:
    arr = griddap_axis(server, dataset, "time", "last")
    return pd.Timestamp(arr[-1])


def tabledap(server: str, dataset: str, variables: Sequence[str],
             constraints: Iterable[str] = (),
             cache_dir: Optional[Path] = None,
             cache_ttl_s: float = 0.0) -> pd.DataFrame:
    """Download a tabledap query as CSV -> DataFrame (units row dropped)."""
    q = ",".join(variables)
    for c in constraints:
        q += "&" + c
    url = f"{server}/tabledap/{dataset}.csv?{quote(q, safe='[]():,.-+*&=<>%')}"
    log.info("tabledap %s", dataset)
    try:
        raw = _get(url, cache_dir, cache_ttl_s)
    except ErddapError as exc:
        if "404" in str(exc):
            log.warning("no rows for %s", dataset)
            return pd.DataFrame(columns=list(variables))
        raise
    df = pd.read_csv(io.BytesIO(raw), skiprows=[1])
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    return df
