"""Build the island outlines the model places its stranding receptors on.

`sargassum/coastline.py` originally carried a hand-traced outline with 5-15 km
between vertices, which put receptors up to 6 km inland or offshore. That was
invisible against a blurred raster basemap; against a real one it is the first
thing you see. This script replaces those outlines with real shoreline.

Source: GSHHS (Global Self-consistent, Hierarchical, High-resolution
Shoreline), Wessel & Smith, distributed under LGPL in the `basemap-data-hires`
wheel. Level 1 is land, level 2 is lakes inside that land.

The output is committed, so a normal checkout never runs this. It is only
needed when the island set or the simplification changes.

Run:  pip install shapely basemap-data basemap-data-hires
      python scripts/build_shoreline.py
Out:  data/static/islands.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import box, Point, Polygon
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "static" / "islands.json"

# Wide enough to capture the three monitored islands with room around them;
# GSHHS is global, and reading all of it to find Culebra would be wasteful.
CLIP = (-68.8, 17.0, -64.2, 19.2)          # lon_min, lat_min, lon_max, lat_max

# The three islands the model monitors, with a seed point that is
# unambiguously inside each one.
ISLAND_SEEDS = {
    "puerto_rico": (-66.40, 18.20),
    "vieques": (-65.42, 18.12),
    "culebra": (-65.30, 18.31),
}

# Receptors sit 5 km apart, so sub-100 m shoreline crenellation cannot move
# one, but it would add thousands of vertices that every run then walks.
TOL = 0.0015   # ~165 m


def _data_dir() -> Path:
    """Locate the GSHHS .dat files shipped by basemap-data(-hires)."""
    try:
        import mpl_toolkits.basemap_data as bd
        # basemap_data is a namespace package, so __file__ is None and the
        # directory has to come off __path__.
        for entry in list(getattr(bd, "__path__", [])):
            if (Path(entry) / "gshhsmeta_h.dat").exists():
                return Path(entry)
    except ImportError:
        pass
    for p in map(Path, sys.path):
        cand = p / "mpl_toolkits" / "basemap_data"
        if (cand / "gshhsmeta_h.dat").exists():
            return cand
    raise SystemExit(
        "GSHHS data not found. Install it with:\n"
        "  pip install basemap-data basemap-data-hires")


def read_gshhs(data_dir: Path, res: str, clip: tuple, levels=(1, 2)):
    """Yield (level, Nx2 lon/lat array) for polygons overlapping `clip`.

    basemap stores GSHHS as a flat float32 stream of lon/lat pairs plus a
    sidecar index. The index line is:
        level  area  npts  south  north  byte_offset  byte_count  id
    There is no longitude in the index, so latitude prunes cheaply and
    longitude is checked after the read.
    """
    meta = data_dir / f"gshhsmeta_{res}.dat"
    dat = data_dir / f"gshhs_{res}.dat"
    if not meta.exists():
        raise SystemExit(f"missing {meta}")

    lon0, lat0, lon1, lat1 = clip
    with open(meta) as fh, open(dat, "rb") as bin_fh:
        for line in fh:
            parts = line.split()
            if len(parts) < 7:
                continue
            level = int(parts[0])
            if level not in levels:
                continue
            south, north = float(parts[3]), float(parts[4])
            if north < lat0 or south > lat1:
                continue
            offset, nbytes = int(parts[5]), int(parts[6])
            bin_fh.seek(offset)
            pts = np.frombuffer(bin_fh.read(nbytes), dtype="<f4")
            pts = pts.reshape(-1, 2).astype("float64")
            # GSHHS ships 0..360; fold into -180..180.
            lon = np.where(pts[:, 0] > 180.0, pts[:, 0] - 360.0, pts[:, 0])
            pts = np.column_stack([lon, pts[:, 1]])
            if pts[:, 0].max() < lon0 or pts[:, 0].min() > lon1:
                continue
            yield level, pts


def build_islands() -> dict:
    """Outlines for the monitored islands, keyed by island.

    Emitted as plain [lon, lat] rings so `coastline.py` can read them with
    nothing but the standard library - the hourly job must not need shapely.
    """
    data_dir = _data_dir()
    res = "f" if (data_dir / "gshhs_f.dat").exists() else "h"
    print(f"reading GSHHS resolution '{res}' from {data_dir}")

    clip_poly = box(*CLIP)
    land, lakes = [], []
    for level, pts in read_gshhs(data_dir, res, CLIP):
        if len(pts) < 4:
            continue
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        poly = poly.intersection(clip_poly)
        if poly.is_empty:
            continue
        (land if level == 1 else lakes).append(poly)
    print(f"  {len(land)} land polygons, {len(lakes)} lakes in clip box")

    polys = [g for p in land for g in getattr(p, "geoms", [p])]

    out = {}
    for name, (slon, slat) in ISLAND_SEEDS.items():
        pt = Point(slon, slat)
        hits = [p for p in polys if p.contains(pt)]
        if not hits:
            # Fall back to nearest, so a slightly misplaced seed degrades to a
            # wrong-but-obvious island rather than a silent missing one.
            hits = [min(polys, key=lambda p: p.distance(pt))]
        poly = max(hits, key=lambda p: p.area).simplify(TOL)
        ring = [[round(x, 5), round(y, 5)]
                for x, y in poly.exterior.coords[:-1]]
        out[name] = ring
        print(f"  {name}: {len(ring)} vertices, "
              f"{poly.length * 111.0:.0f} km perimeter")
    return out


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build_islands(), separators=(",", ":")))
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
