"""AFAI -> Sargassum fractional coverage -> wet biomass.

Method follows the USF/NOAA AFAI approach:
  * dAFAI = AFAI - local background AFAI of Sargassum-free water
  * fractional coverage = dAFAI / AFAI(100% coverage), clipped to [0, 1]
  * wet biomass density = coverage * 3.34 kg m-2

References
----------
Wang & Hu (2016, 2018); Wang et al. (2019) Science; and the AFAI unmixing
described in the USF Sargassum Watch System documentation, where
dAFAI = 4.41e-2 corresponds to 100% sub-pixel coverage and a mean conversion
factor of 3.34 kg wet biomass m-2 converts areal coverage to biomass.
"""
from __future__ import annotations

import numpy as np
import xarray as xr
from scipy.ndimage import percentile_filter, uniform_filter


def local_background(afai: np.ndarray, window: int = 41,
                     percentile: float = 25.0) -> np.ndarray:
    """Low percentile of a moving window = 'Sargassum-free' water baseline."""
    filled = np.where(np.isfinite(afai), afai, np.nan)
    # percentile_filter cannot handle NaN, so work on a filled copy and mask
    med = np.nanmedian(filled)
    if not np.isfinite(med):
        med = 0.0
    work = np.where(np.isfinite(filled), filled, med)
    win = max(3, int(window) | 1)
    bg = percentile_filter(work, percentile=percentile, size=win, mode="nearest")
    # light smoothing so the background does not chase individual mats
    return uniform_filter(bg, size=max(3, win // 4))


def noise_sigma(d: np.ndarray) -> float:
    """Robust noise scale of dAFAI, estimated from its negative tail.

    Sargassum only ever *raises* AFAI above the local water background, so the
    negative half of the dAFAI distribution is pure noise. A median-absolute
    estimate on that half is insensitive to how much Sargassum is present.
    """
    neg = d[np.isfinite(d) & (d < 0)]
    if neg.size < 50:
        return 0.0
    return float(1.4826 * np.median(np.abs(neg)))


def afai_to_coverage(afai: np.ndarray, cfg) -> np.ndarray:
    full = float(cfg.get_path("biomass.afai_full_coverage", 0.0441))
    win = int(cfg.get_path("biomass.background_window_px", 41))
    pct = float(cfg.get_path("biomass.background_percentile", 25))
    min_cov = float(cfg.get_path("biomass.min_coverage", 0.0005))
    n_sigma = float(cfg.get_path("biomass.detect_sigma", 3.0))

    bg = local_background(afai, win, pct)
    d = afai - bg
    thresh = max(n_sigma * noise_sigma(d), min_cov * full)
    # subtract the threshold so detection is continuous rather than a step
    cov = np.clip((d - thresh) / full, 0.0, 1.0)

    # Despeckle: real Sargassum shows up as lines and patches spanning several
    # pixels; an isolated hot pixel with no detected neighbours is noise that
    # happened to clear the threshold.
    min_nb = int(cfg.get_path("biomass.min_neighbours", 2))
    if min_nb > 0:
        det = (cov > 0).astype(float)
        neighbours = uniform_filter(det, size=3, mode="constant") * 9.0 - det
        cov = np.where(neighbours >= min_nb, cov, 0.0)

    cov[~np.isfinite(afai)] = np.nan
    return cov


def coverage_to_wet_kg_m2(cov: np.ndarray, cfg) -> np.ndarray:
    k = float(cfg.get_path("biomass.wet_kg_per_m2_full_coverage", 3.34))
    return cov * k


def cell_area_m2(lat: np.ndarray, dlat: float, dlon: float) -> np.ndarray:
    """Area of each grid cell (m2) as a column vector broadcastable to (lat,lon)."""
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * np.cos(np.radians(lat))
    return (dlat * m_per_deg_lat) * (dlon * m_per_deg_lon)


def biomass_field(ds: xr.Dataset, cfg, var: str = "AFAI") -> xr.Dataset:
    """Return a Dataset with coverage, wet density (kg/m2) and cell mass (kg)."""
    da = ds[var]
    if "time" in da.dims:
        da = da.isel(time=-1)
    lat = np.asarray(da["latitude"].values, dtype=float)
    lon = np.asarray(da["longitude"].values, dtype=float)
    arr = np.asarray(da.values, dtype=float)

    cov = afai_to_coverage(arr, cfg)
    dens = coverage_to_wet_kg_m2(cov, cfg)

    dlat = float(abs(np.median(np.diff(lat)))) if lat.size > 1 else 0.015
    dlon = float(abs(np.median(np.diff(lon)))) if lon.size > 1 else 0.015
    area = cell_area_m2(lat, dlat, dlon)[:, None] * np.ones((1, lon.size))
    mass = np.where(np.isfinite(dens), dens, 0.0) * area

    out = xr.Dataset(
        {
            "coverage": (("latitude", "longitude"), cov),
            "wet_kg_m2": (("latitude", "longitude"), dens),
            "cell_mass_kg": (("latitude", "longitude"), mass),
            "cell_area_m2": (("latitude", "longitude"), area),
        },
        coords={"latitude": lat, "longitude": lon},
    )
    if "time" in ds.coords:
        out.attrs["time"] = str(np.asarray(ds["time"].values).ravel()[-1])
    out.attrs["total_wet_tonnes"] = float(np.nansum(mass) / 1000.0)
    return out
