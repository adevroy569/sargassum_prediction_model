"""Coarse Puerto Rico / Vieques / Culebra shoreline, resampled into named
beach segments with outward (seaward) normals.

The outlines below are deliberately coarse (~5-15 km between vertices). They
are used only to (a) place stranding receptors and (b) compute a seaward
normal direction; they are not a navigational coastline. Replace
`ISLANDS` with a real shapefile-derived polygon at any time - everything
downstream works off the resampled segment list.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Sequence, Tuple

EARTH_R = 6371000.0

# (lon, lat) vertices, traced around each island.
ISLANDS = {
    "puerto_rico": [
        (-67.16, 18.50), (-67.05, 18.49), (-66.95, 18.50), (-66.83, 18.49),
        (-66.72, 18.48), (-66.61, 18.49), (-66.53, 18.48), (-66.40, 18.47),
        (-66.28, 18.47), (-66.20, 18.47), (-66.11, 18.46), (-66.03, 18.46),
        (-65.96, 18.45), (-65.87, 18.44), (-65.79, 18.43), (-65.72, 18.40),
        (-65.66, 18.38), (-65.62, 18.35), (-65.62, 18.30), (-65.65, 18.24),
        (-65.68, 18.19), (-65.72, 18.14), (-65.76, 18.09), (-65.79, 18.04),
        (-65.85, 17.99), (-65.93, 17.96), (-66.02, 17.95), (-66.11, 17.94),
        (-66.20, 17.94), (-66.30, 17.94), (-66.41, 17.94), (-66.52, 17.95),
        (-66.62, 17.96), (-66.72, 17.96), (-66.81, 17.95), (-66.90, 17.94),
        (-66.99, 17.95), (-67.06, 17.96), (-67.13, 17.95), (-67.19, 17.93),
        (-67.20, 18.00), (-67.19, 18.08), (-67.19, 18.16), (-67.17, 18.24),
        (-67.19, 18.31), (-67.26, 18.36), (-67.24, 18.43),
    ],
    "vieques": [
        (-65.58, 18.15), (-65.50, 18.16), (-65.42, 18.16), (-65.34, 18.15),
        (-65.28, 18.13), (-65.26, 18.11), (-65.31, 18.09), (-65.38, 18.08),
        (-65.46, 18.09), (-65.54, 18.11),
    ],
    "culebra": [
        (-65.33, 18.33), (-65.29, 18.34), (-65.26, 18.32), (-65.27, 18.29),
        (-65.31, 18.29),
    ],
}

# Landmarks used to give every segment a human-readable name.
LANDMARKS: List[Tuple[str, float, float]] = [
    ("Aguadilla / Crash Boat", -67.16, 18.46),
    ("Isabela / Jobos", -67.03, 18.51),
    ("Quebradillas", -66.94, 18.49),
    ("Arecibo", -66.72, 18.48),
    ("Barceloneta / Punta Morillos", -66.55, 18.49),
    ("Vega Baja / Manatí", -66.38, 18.48),
    ("Dorado", -66.26, 18.47),
    ("Toa Baja / Cataño", -66.15, 18.46),
    ("San Juan / Condado", -66.07, 18.47),
    ("Isla Verde / Carolina", -65.98, 18.45),
    ("Loíza / Piñones", -65.88, 18.44),
    ("Río Grande", -65.80, 18.43),
    ("Luquillo", -65.72, 18.39),
    ("Fajardo / Las Cabezas", -65.63, 18.37),
    ("Fajardo / Sardinera", -65.62, 18.32),
    ("Ceiba / Roosevelt Roads", -65.64, 18.24),
    ("Naguabo", -65.70, 18.17),
    ("Humacao / Punta Santiago", -65.75, 18.11),
    ("Yabucoa", -65.81, 18.03),
    ("Maunabo / Punta Tuna", -65.89, 17.97),
    ("Patillas", -66.01, 17.95),
    ("Arroyo", -66.06, 17.94),
    ("Guayama / Punta Pozuelo", -66.15, 17.93),
    ("Salinas", -66.28, 17.94),
    ("Santa Isabel", -66.40, 17.94),
    ("Juana Díaz", -66.51, 17.95),
    ("Ponce", -66.62, 17.96),
    ("Peñuelas / Tallaboa", -66.72, 17.96),
    ("Guayanilla", -66.79, 17.96),
    ("Guánica", -66.90, 17.94),
    ("Lajas / La Parguera", -67.04, 17.96),
    ("Cabo Rojo / El Combate", -67.18, 17.95),
    ("Boquerón", -67.18, 18.02),
    ("Joyuda", -67.19, 18.12),
    ("Mayagüez", -67.16, 18.21),
    ("Añasco / Rincón", -67.22, 18.32),
    ("Rincón / Punta Higüero", -67.26, 18.36),
    ("Aguada", -67.20, 18.42),
    ("Vieques / Esperanza", -65.47, 18.09),
    ("Vieques / Sun Bay", -65.42, 18.09),
    ("Vieques / Isabel Segunda", -65.44, 18.15),
    ("Vieques / Green Beach", -65.57, 18.13),
    ("Vieques / Punta Este", -65.27, 18.12),
    ("Culebra / Flamenco", -65.31, 18.34),
    ("Culebra / Dewey", -65.30, 18.30),
    ("Culebra / Zoni", -65.26, 18.32),
]


@dataclass
class Segment:
    seg_id: str
    name: str
    island: str
    lat: float
    lon: float
    normal_lat: float   # unit seaward normal, north component
    normal_lon: float   # unit seaward normal, east component
    length_m: float
    coast: str          # north / east / south / west


def _haversine(lon1, lat1, lon2, lat2) -> float:
    p = math.pi / 180.0
    dlat = (lat2 - lat1) * p
    dlon = (lon2 - lon1) * p
    a = (math.sin(dlat / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin(dlon / 2) ** 2)
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def _resample(poly: Sequence[Tuple[float, float]], spacing_m: float):
    """Walk a closed polygon and emit points every `spacing_m` along it."""
    pts = list(poly) + [poly[0]]
    out = []
    carry = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(pts[:-1], pts[1:]):
        seg_len = _haversine(lon1, lat1, lon2, lat2)
        if seg_len <= 0:
            continue
        d = spacing_m - carry
        while d < seg_len:
            f = d / seg_len
            out.append((lon1 + f * (lon2 - lon1), lat1 + f * (lat2 - lat1),
                        lon2 - lon1, lat2 - lat1))
            d += spacing_m
        carry = seg_len - (d - spacing_m)
    return out


def _centroid(poly) -> Tuple[float, float]:
    return (sum(p[0] for p in poly) / len(poly),
            sum(p[1] for p in poly) / len(poly))


def _coast_face(nlon: float, nlat: float) -> str:
    ang = math.degrees(math.atan2(nlat, nlon))  # 0 = east, 90 = north
    if -45 <= ang < 45:
        return "east"
    if 45 <= ang < 135:
        return "north"
    if ang >= 135 or ang < -135:
        return "west"
    return "south"


def build_segments(spacing_km: float = 5.0) -> List[Segment]:
    segs: List[Segment] = []
    for island, poly in ISLANDS.items():
        cx, cy = _centroid(poly)
        for i, (lon, lat, dlon, dlat) in enumerate(
                _resample(poly, spacing_km * 1000.0)):
            # tangent in local metres
            mlon = math.cos(math.radians(lat))
            tx, ty = dlon * mlon, dlat
            norm = math.hypot(tx, ty) or 1.0
            tx, ty = tx / norm, ty / norm
            # candidate normal (rotate tangent +90 deg)
            nx, ny = -ty, tx
            # flip so it points away from the island centroid
            if (lon - cx) * mlon * nx + (lat - cy) * ny < 0:
                nx, ny = -nx, -ny
            name = min(LANDMARKS,
                       key=lambda L: _haversine(lon, lat, L[1], L[2]))[0]
            segs.append(Segment(
                seg_id=f"{island[:2].upper()}{i:03d}",
                name=name,
                island=island,
                lat=round(lat, 5),
                lon=round(lon, 5),
                normal_lat=round(ny, 5),
                normal_lon=round(nx, 5),
                length_m=spacing_km * 1000.0,
                coast=_coast_face(nx, ny),
            ))
    return segs


def to_geojson(segs: Sequence[Segment]) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [s.lon, s.lat]},
                "properties": asdict(s),
            }
            for s in segs
        ],
    }


def load_segments(path: Path) -> List[Segment]:
    gj = json.loads(Path(path).read_text())
    return [Segment(**f["properties"]) for f in gj["features"]]


def write_segments(path: Path, spacing_km: float = 5.0) -> List[Segment]:
    segs = build_segments(spacing_km)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(to_geojson(segs), indent=1))
    return segs


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "data/static/coast_segments.geojson"
    s = write_segments(out)
    print(f"{len(s)} segments -> {out}")
