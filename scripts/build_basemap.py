"""Build the site's self-hosted vector basemap from GSHHS shoreline data.

The site used to draw a raster tile basemap from a third-party CDN. That has
two problems: the provider can start demanding an API key at any time (and
did - tiles came back stamped API KEY REQUIRED), and raster tiles are
resolution-locked, so the map goes soft between native zoom levels.

This script bakes the shoreline into the repository as GeoJSON instead.
MapLibre then draws it as vector geometry, which stays sharp at every zoom,
costs no network requests, and cannot be revoked.

Source: GSHHS (Global Self-consistent, Hierarchical, High-resolution
Shoreline), Wessel & Smith, distributed under LGPL in the `basemap-data-hires`
wheel. Level 1 is land, level 2 is lakes inside that land.

The same source also produces the island outlines the *model* places its
stranding receptors on. `sargassum/coastline.py` originally carried a
hand-traced outline with 5-15 km between vertices, which put receptors up to
6 km inland or offshore. That was invisible against a blurred raster
basemap; against a real shoreline it is the first thing you see.

Run:  python scripts/build_basemap.py
Out:  site/data/basemap_land.geojson    - what the map draws
      site/data/basemap_places.geojson  - coastal labels
      data/static/islands.json          - what the model places receptors on
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import box, mapping, Point, Polygon
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "site" / "data"

# The map opens at zoom 7.4 on Puerto Rico and clamps to minZoom 5. At zoom 5
# a wide viewport spans roughly 26 degrees of longitude, so the basemap has to
# carry the surrounding Caribbean or the island floats in an empty void.
CLIP = (-85.0, 8.0, -55.0, 28.0)          # lon_min, lat_min, lon_max, lat_max

# Inside this box the coastline is kept at full GSHHS resolution: it is the
# shoreline the model actually strands onto, and it is what the user zooms to.
DETAIL = (-68.6, 17.2, -64.2, 19.0)

TOL_DETAIL = 0.00012   # ~13 m  - below one screen pixel at maxZoom 12
TOL_CONTEXT = 0.0045   # ~500 m - invisible at the zooms context land is seen at

# Two area floors for the same reason there are two tolerances. A 0.05 km2 cay
# off Fajardo is a real landmark when you are zoomed to Fajardo; a 0.05 km2 cay
# in the Bahamas is one pixel of noise and a few hundred KB of payload.
MIN_AREA_DETAIL_KM2 = 0.04
MIN_AREA_CONTEXT_KM2 = 3.0


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
            # GSHHS ships 0..360; fold into -180..180 to match the map.
            lon = np.where(pts[:, 0] > 180.0, pts[:, 0] - 360.0, pts[:, 0])
            pts = np.column_stack([lon, pts[:, 1]])
            if pts[:, 0].max() < lon0 or pts[:, 0].min() > lon1:
                continue
            yield level, pts


def build_land() -> dict:
    data_dir = _data_dir()
    res = "f" if (data_dir / "gshhs_f.dat").exists() else "h"
    print(f"reading GSHHS resolution '{res}' from {data_dir}")

    clip_poly = box(CLIP[0], CLIP[1], CLIP[2], CLIP[3])
    detail_poly = box(DETAIL[0], DETAIL[1], DETAIL[2], DETAIL[3])

    land, lakes = [], []
    for level, pts in read_gshhs(data_dir, res, CLIP):
        if len(pts) < 4:
            continue
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            continue
        poly = poly.intersection(clip_poly)
        if poly.is_empty:
            continue
        (land if level == 1 else lakes).append(poly)

    print(f"  {len(land)} land polygons, {len(lakes)} lakes in clip box")

    land_u = unary_union(land)
    if lakes:
        land_u = land_u.difference(unary_union(lakes))

    # Two-tier simplification. Everything inside the detail box keeps full
    # shoreline fidelity; everything outside is decimated hard, because it is
    # only ever seen from far away and it is what dominates the file size.
    near = land_u.intersection(detail_poly).simplify(TOL_DETAIL)
    far = land_u.difference(detail_poly).simplify(TOL_CONTEXT)

    # ~12,300 km2 per square degree at this latitude.
    DEG2_KM2 = 12300.0

    def prune(geom, floor_km2):
        parts = list(getattr(geom, "geoms", [geom]))
        return [g for g in parts
                if not g.is_empty and g.area * DEG2_KM2 >= floor_km2]

    keep = (prune(near, MIN_AREA_DETAIL_KM2)
            + prune(far, MIN_AREA_CONTEXT_KM2))
    print(f"  kept {len(keep)} polygons after area filter")

    feats = [{"type": "Feature", "properties": {}, "geometry": mapping(g)}
             for g in keep]
    return {"type": "FeatureCollection", "features": feats}


# The three islands the model monitors, with a seed point that is
# unambiguously inside each one.
ISLAND_SEEDS = {
    "puerto_rico": (-66.40, 18.20),
    "vieques": (-65.42, 18.12),
    "culebra": (-65.30, 18.31),
}

# Receptor outlines are simplified harder than the drawn coastline. Receptors
# sit 5 km apart, so sub-100 m shoreline crenellation cannot move one, but it
# does add thousands of vertices the model would walk on every run.
TOL_RECEPTOR = 0.0015   # ~165 m


def build_islands(land_fc: dict) -> dict:
    """Outlines for the monitored islands, for stranding receptor placement.

    Emitted as plain [lon, lat] rings keyed by island so `coastline.py` can
    read them with nothing but the standard library.
    """
    from shapely.geometry import shape

    polys = [shape(f["geometry"]) for f in land_fc["features"]]
    out = {}
    for name, (slon, slat) in ISLAND_SEEDS.items():
        pt = Point(slon, slat)
        hits = [p for p in polys if p.contains(pt)]
        if not hits:
            # Fall back to nearest, so a slightly misplaced seed degrades to a
            # wrong-but-obvious island rather than a silent missing one.
            hits = [min(polys, key=lambda p: p.distance(pt))]
        poly = max(hits, key=lambda p: p.area).simplify(TOL_RECEPTOR)
        ring = [[round(x, 5), round(y, 5)]
                for x, y in poly.exterior.coords[:-1]]
        out[name] = ring
        print(f"  {name}: {len(ring)} vertices, "
              f"{poly.length * 111.0:.0f} km perimeter")
    return out


def build_places() -> dict:
    """Coastal labels, reused from the shoreline module's landmark list so the
    map never disagrees with the segment names in the popups."""
    sys.path.insert(0, str(ROOT))
    from sargassum.coastline import LANDMARKS

    feats = []
    for name, lon, lat in LANDMARKS:
        # Popups already carry the full "Town / Beach" string; the label on the
        # map wants the town only, or it collides with its neighbours.
        short = name.split(" / ")[0].strip()
        feats.append({
            "type": "Feature",
            "properties": {"name": short},
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
        })
    return {"type": "FeatureCollection", "features": feats}


def write(obj: dict, path: Path, precision: int = 5) -> None:
    """Round coordinates before writing. 5 decimals is ~1.1 m, well under the
    resolution of anything on this map, and it roughly halves the file."""
    def rnd(o):
        if isinstance(o, float):
            return round(o, precision)
        if isinstance(o, list):
            return [rnd(v) for v in o]
        if isinstance(o, dict):
            return {k: rnd(v) for k, v in o.items()}
        return o

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rnd(obj), separators=(",", ":")))
    print(f"wrote {path.relative_to(ROOT)}  "
          f"({path.stat().st_size / 1024:.0f} KB)")


def main() -> None:
    land = build_land()
    write(land, OUT_DIR / "basemap_land.geojson")
    write(build_places(), OUT_DIR / "basemap_places.geojson")
    write(build_islands(land), ROOT / "data" / "static" / "islands.json")


if __name__ == "__main__":
    main()
