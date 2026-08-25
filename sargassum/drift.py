"""Lagrangian drift of Sargassum rafts.

Velocity model
--------------
    u_raft = u_current + windage * u_wind10 + random walk

`u_current` comes from the CariCOOS HF-radar surface velocity field. HF radar
is an *observation*, not a forecast, so for lead times beyond "now" we relax
the latest field toward its recent mean with an e-folding time
(`persistence_efold_hours`). Winds do come from a forecast model (WRF-NMM),
so the windage term is genuinely predictive.

This is a first-order transport model. It does not resolve Langmuir cells,
raft aggregation/sinking, or growth/mortality.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

M_PER_DEG_LAT = 111_320.0


@dataclass
class VectorField:
    """Regular lat/lon vector field with bilinear sampling and NaN handling."""
    lat: np.ndarray
    lon: np.ndarray
    u: np.ndarray            # (nlat, nlon)
    v: np.ndarray

    def _idx(self, lat, lon):
        i = np.interp(lat, self.lat, np.arange(self.lat.size),
                      left=np.nan, right=np.nan)
        j = np.interp(lon, self.lon, np.arange(self.lon.size),
                      left=np.nan, right=np.nan)
        return i, j

    def sample(self, lat, lon) -> Tuple[np.ndarray, np.ndarray]:
        i, j = self._idx(lat, lon)
        ok = np.isfinite(i) & np.isfinite(j)
        u = np.zeros_like(lat, dtype=float)
        v = np.zeros_like(lat, dtype=float)
        if not ok.any():
            return u, v
        i0 = np.clip(np.floor(np.where(ok, i, 0)).astype(int), 0, self.lat.size - 2)
        j0 = np.clip(np.floor(np.where(ok, j, 0)).astype(int), 0, self.lon.size - 2)
        fi = np.where(ok, i, 0) - i0
        fj = np.where(ok, j, 0) - j0
        for arr, out in ((self.u, u), (self.v, v)):
            a = arr[i0, j0]; b = arr[i0, j0 + 1]
            c = arr[i0 + 1, j0]; d = arr[i0 + 1, j0 + 1]
            w = np.stack([(1 - fi) * (1 - fj), (1 - fi) * fj,
                          fi * (1 - fj), fi * fj])
            vals = np.stack([a, b, c, d])
            good = np.isfinite(vals)
            w = np.where(good, w, 0.0)
            tot = w.sum(axis=0)
            res = np.where(tot > 0, (np.where(good, vals, 0.0) * w).sum(axis=0)
                           / np.where(tot > 0, tot, 1.0), 0.0)
            out[:] = np.where(ok, res, 0.0)
        return u, v

    @staticmethod
    def zeros_like_grid(lat, lon) -> "VectorField":
        z = np.zeros((len(lat), len(lon)))
        return VectorField(np.asarray(lat), np.asarray(lon), z, z.copy())


@dataclass
class Particles:
    lat: np.ndarray
    lon: np.ndarray
    mass_kg: np.ndarray
    active: np.ndarray = field(default=None)  # type: ignore[assignment]
    beached_seg: np.ndarray = field(default=None)  # type: ignore[assignment]
    age_h: np.ndarray = field(default=None)  # type: ignore[assignment]

    def __post_init__(self):
        n = self.lat.size
        if self.active is None:
            self.active = np.ones(n, dtype=bool)
        if self.beached_seg is None:
            self.beached_seg = np.full(n, -1, dtype=int)
        if self.age_h is None:
            self.age_h = np.zeros(n, dtype=float)

    def __len__(self):
        return self.lat.size


def seed_from_field(bio, cfg, max_particles: int = 20000) -> Particles:
    """One particle per grid cell that contains Sargassum, carrying its mass."""
    mass = np.asarray(bio["cell_mass_kg"].values)
    lat = np.asarray(bio["latitude"].values)
    lon = np.asarray(bio["longitude"].values)
    ii, jj = np.where(np.isfinite(mass) & (mass > 0))
    if ii.size == 0:
        return Particles(np.array([]), np.array([]), np.array([]))
    m = mass[ii, jj]
    if ii.size > max_particles:
        # keep the heaviest cells, redistribute the discarded mass onto them
        keep = np.argsort(m)[::-1][:max_particles]
        dropped = m.sum() - m[keep].sum()
        ii, jj, m = ii[keep], jj[keep], m[keep]
        m = m + dropped * (m / m.sum())
    return Particles(lat=lat[ii].astype(float), lon=lon[jj].astype(float),
                     mass_kg=m.astype(float))


def blended_current(latest: VectorField, mean: Optional[VectorField],
                    lead_h: float, efold_h: float) -> VectorField:
    """Persistence forecast relaxing toward the recent-mean circulation."""
    if mean is None:
        return latest
    w = float(np.exp(-max(lead_h, 0.0) / max(efold_h, 1e-6)))
    return VectorField(latest.lat, latest.lon,
                       w * latest.u + (1 - w) * mean.u,
                       w * latest.v + (1 - w) * mean.v)


def step(p: Particles, cur: VectorField, wind: Optional[VectorField],
         dt_h: float, cfg, rng: np.random.Generator) -> None:
    """Advance active particles one step (RK2 / Heun in metres-per-degree space)."""
    a = p.active
    if not a.any():
        return
    windage = float(cfg.get_path("drift.windage", 0.012))
    kh = float(cfg.get_path("drift.horizontal_diffusivity", 30.0))
    dt = dt_h * 3600.0

    def velocity(lat, lon):
        u, v = cur.sample(lat, lon)
        if wind is not None:
            uw, vw = wind.sample(lat, lon)
            u = u + windage * uw
            v = v + windage * vw
        return u, v

    lat0, lon0 = p.lat[a], p.lon[a]
    u1, v1 = velocity(lat0, lon0)
    mlon0 = M_PER_DEG_LAT * np.cos(np.radians(lat0))
    lat1 = lat0 + (v1 * dt) / M_PER_DEG_LAT
    lon1 = lon0 + (u1 * dt) / np.where(mlon0 > 1, mlon0, 1)
    u2, v2 = velocity(lat1, lon1)
    u, v = 0.5 * (u1 + u2), 0.5 * (v1 + v2)

    sigma = np.sqrt(2.0 * kh * dt)
    du = rng.normal(0.0, sigma, size=lat0.size)
    dv = rng.normal(0.0, sigma, size=lat0.size)

    mlon = M_PER_DEG_LAT * np.cos(np.radians(lat0))
    p.lat[a] = lat0 + (v * dt + dv) / M_PER_DEG_LAT
    p.lon[a] = lon0 + (u * dt + du) / np.where(mlon > 1, mlon, 1)
    p.age_h[a] += dt_h
    return None


def velocity_at(p: Particles, cur: VectorField, wind: Optional[VectorField],
                cfg) -> Tuple[np.ndarray, np.ndarray]:
    """Instantaneous raft velocity (m/s) for every particle."""
    windage = float(cfg.get_path("drift.windage", 0.012))
    u, v = cur.sample(p.lat, p.lon)
    if wind is not None:
        uw, vw = wind.sample(p.lat, p.lon)
        u = u + windage * uw
        v = v + windage * vw
    return u, v
