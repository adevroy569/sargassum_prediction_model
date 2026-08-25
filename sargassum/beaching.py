"""Shoreline capture: turn drifting raft mass into stranded wrack per segment.

At every time step a particle inside the capture zone of its nearest coast
segment loses a fraction of its mass to that segment:

    onshore_speed = -(u, v) . seaward_normal          [m/s, >0 = heading ashore]
    fraction      = efficiency * min(1, onshore_speed * dt / capture_distance)

which is mass-conserving (nothing is created) and reduces to "everything that
crosses the capture zone strands, times an efficiency" in the limit of strong
onshore flow.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import numpy as np
from scipy.spatial import cKDTree

from matplotlib.path import Path as MplPath

from .coastline import ISLANDS, Segment

M_PER_DEG_LAT = 111_320.0
LAT0 = 18.2  # reference latitude for the local projection


def _to_xy(lat, lon):
    x = np.asarray(lon, dtype=float) * M_PER_DEG_LAT * np.cos(np.radians(LAT0))
    y = np.asarray(lat, dtype=float) * M_PER_DEG_LAT
    return np.column_stack([x, y])


class LandMask:
    """Point-in-polygon test against the island outlines."""

    def __init__(self, buffer_deg: float = 0.0):
        self.paths = []
        for poly in ISLANDS.values():
            self.paths.append(MplPath(np.asarray(poly, dtype=float)))
        self.buffer = buffer_deg

    def inside(self, lat, lon) -> np.ndarray:
        pts = np.column_stack([np.asarray(lon, dtype=float),
                               np.asarray(lat, dtype=float)])
        hit = np.zeros(pts.shape[0], dtype=bool)
        for path in self.paths:
            hit |= path.contains_points(pts, radius=self.buffer)
        return hit


@dataclass
class BeachingAccumulator:
    segments: List[Segment]
    n_steps: int

    def __post_init__(self):
        self.tree = cKDTree(_to_xy([s.lat for s in self.segments],
                                   [s.lon for s in self.segments]))
        self.normals = np.array([[s.normal_lon, s.normal_lat]
                                 for s in self.segments], dtype=float)
        self.length_m = np.array([s.length_m for s in self.segments],
                                 dtype=float)
        # stranded wet mass, kg, indexed [segment, step]
        self.mass = np.zeros((len(self.segments), self.n_steps), dtype=float)

    def capture(self, p, u, v, step_idx: int, dt_h: float, cfg) -> float:
        """Move mass from particles to segments. Returns kg stranded."""
        act = p.active
        if not act.any():
            return 0.0
        capture_m = float(cfg.get_path("beaching.capture_km", 4.0)) * 1000.0
        eff = float(cfg.get_path("beaching.onshore_efficiency", 0.55))
        min_on = float(cfg.get_path("beaching.min_onshore_speed", 0.02))
        dt_s = dt_h * 3600.0

        idx = np.flatnonzero(act)
        xy = _to_xy(p.lat[idx], p.lon[idx])
        dist, seg = self.tree.query(xy, k=1,
                                    distance_upper_bound=capture_m)
        near = np.isfinite(dist) & (seg < len(self.segments))
        if not near.any():
            return 0.0

        sel = idx[near]
        segs = seg[near]
        n = self.normals[segs]
        # onshore = component of raft velocity opposite the seaward normal
        onshore = -(u[sel] * n[:, 0] + v[sel] * n[:, 1])
        go = onshore > min_on
        if not go.any():
            return 0.0

        sel, segs, onshore = sel[go], segs[go], onshore[go]
        frac = eff * np.clip(onshore * dt_s / capture_m, 0.0, 1.0)
        moved = p.mass_kg[sel] * frac
        np.add.at(self.mass, (segs, step_idx), moved)
        p.mass_kg[sel] -= moved
        p.beached_seg[sel] = segs
        p.active[sel[p.mass_kg[sel] < 1.0]] = False
        return float(moved.sum())

    def block_land(self, p, land: "LandMask", prev_lat, prev_lon) -> int:
        """Land is a barrier, not a sink.

        A raft that would move onto land is put back where it was; whether it
        actually strands is decided by `capture`, so the coarse island outline
        cannot inflate the stranded totals.
        """
        act = np.flatnonzero(p.active)
        if act.size == 0:
            return 0
        on_land = land.inside(p.lat[act], p.lon[act])
        if not on_land.any():
            return 0
        sel = act[on_land]
        p.lat[sel] = prev_lat[sel]
        p.lon[sel] = prev_lon[sel]
        return int(sel.size)

    # ---- summaries -------------------------------------------------------
    def totals_kg(self) -> np.ndarray:
        return self.mass.sum(axis=1)

    def kg_per_m(self) -> np.ndarray:
        return self.totals_kg() / np.where(self.length_m > 0, self.length_m, 1)

    def daily_kg(self, dt_h: float) -> np.ndarray:
        """Reshape the per-step matrix into per-day totals [segment, day]."""
        per_day = max(1, int(round(24.0 / dt_h)))
        n_days = int(np.ceil(self.n_steps / per_day))
        pad = n_days * per_day - self.n_steps
        m = np.pad(self.mass, ((0, 0), (0, pad)))
        return m.reshape(len(self.segments), n_days, per_day).sum(axis=2)
