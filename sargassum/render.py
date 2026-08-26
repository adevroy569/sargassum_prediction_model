"""Static maps and the JSON/GeoJSON contract the website consumes."""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, LogNorm  # noqa: E402

from . import emissions as emis_mod  # noqa: E402
from .coastline import ISLANDS  # noqa: E402
from .palette import BLUE_RAMP, INK, ORANGE_RAMP, STATUS  # noqa: E402

log = logging.getLogger(__name__)

CMAP_BIOMASS = LinearSegmentedColormap.from_list("sarg_blue", BLUE_RAMP)
CMAP_BEACH = LinearSegmentedColormap.from_list("sarg_orange", ORANGE_RAMP)


def _set_extent(ax, extent=None):
    lon0, lon1, lat0, lat1 = extent or ISLAND_EXTENT
    ax.set_xlim(lon0, lon1)
    ax.set_ylim(lat0, lat1)
    ax.set_aspect(1 / np.cos(np.radians((lat0 + lat1) / 2)))


def _draw_land(ax):
    for poly in ISLANDS.values():
        xs = [p[0] for p in poly] + [poly[0][0]]
        ys = [p[1] for p in poly] + [poly[0][1]]
        ax.fill(xs, ys, color=INK["land"], zorder=3, linewidth=0.8,
                edgecolor=INK["land_edge"])


ISLAND_EXTENT = (-67.50, -65.10, 17.82, 18.62)  # lon0, lon1, lat0, lat1


def _label_top(ax, lons, lats, vals, names, unit, n=4):
    """Annotate the n largest values with staggered offsets to avoid overlap."""
    order = [i for i in np.argsort(vals)[::-1][:n] if vals[i] > 0]
    offsets = [(10, 14), (10, -20), (-12, 16), (-12, -22)]
    for k, i in enumerate(order):
        dx, dy = offsets[k % len(offsets)]
        ax.annotate(f"{names[i]}  {vals[i]:,.0f} {unit}",
                    (lons[i], lats[i]), textcoords="offset points",
                    xytext=(dx, dy), fontsize=7.5, color=INK["primary"],
                    ha="left" if dx > 0 else "right", zorder=7,
                    bbox=dict(boxstyle="round,pad=0.28", fc=INK["surface"],
                              ec=INK["axis"], lw=0.5),
                    arrowprops=dict(arrowstyle="-", color=INK["axis"], lw=0.7))


def _style(ax, title: str, subtitle: str):
    ax.set_facecolor(INK["surface"])
    ax.grid(True, color=INK["grid"], linewidth=0.5, zorder=0)
    ax.set_xlabel("Longitude", color=INK["secondary"], fontsize=9)
    ax.set_ylabel("Latitude", color=INK["secondary"], fontsize=9)
    ax.tick_params(colors=INK["muted"], labelsize=8)
    for s in ax.spines.values():
        s.set_color(INK["axis"])
    ax.set_title(title, color=INK["primary"], fontsize=13, loc="left",
                 pad=26, fontweight="600")
    ax.text(0, 1.012, subtitle, transform=ax.transAxes, fontsize=8.5,
            color=INK["secondary"], va="bottom")


def map_offshore_biomass(res, out: Path) -> Path:
    bio = res.biomass_map
    lat = np.asarray(bio["latitude"].values)
    lon = np.asarray(bio["longitude"].values)
    dens = np.asarray(bio["wet_kg_m2"].values, dtype=float)
    dens = np.where(np.isfinite(dens) & (dens > 0), dens, np.nan)

    fig, ax = plt.subplots(figsize=(11, 6.2), dpi=140,
                           facecolor=INK["surface"])
    vmax = float(np.nanpercentile(dens, 99.5)) if np.isfinite(dens).any() else 1.0
    vmax = max(vmax, 0.02)
    pcm = ax.pcolormesh(lon, lat, dens, cmap=CMAP_BIOMASS, shading="auto",
                        norm=LogNorm(vmin=max(vmax / 400, 1e-3), vmax=vmax),
                        zorder=2)
    _draw_land(ax)
    cb = fig.colorbar(pcm, ax=ax, pad=0.02, fraction=0.03)
    cb.set_label("Wet Sargassum, kg per m² of sea surface", fontsize=9,
                 color=INK["secondary"])
    cb.ax.tick_params(colors=INK["muted"], labelsize=8)
    scene = pd.Timestamp(res.afai_time).strftime("%Y-%m-%d") \
        if res.afai_time is not None else "unknown"
    _style(ax, "Offshore Sargassum — observed",
           f"USF AFAI {res.afai_product} composite · scene {scene} · "
           f"{res.total_offshore_tonnes:,.0f} t wet in the model domain")
    ax.set_aspect(1 / np.cos(np.radians(float(np.mean(lat)))))
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=INK["surface"], bbox_inches="tight")
    plt.close(fig)
    return out


def map_beaching(res, kg_per_m: np.ndarray, out: Path) -> Path:
    segs = res.segments
    lons = np.array([s.lon for s in segs])
    lats = np.array([s.lat for s in segs])
    vals = np.asarray(kg_per_m, dtype=float)

    fig, ax = plt.subplots(figsize=(12, 5.2), dpi=140,
                           facecolor=INK["surface"])
    _draw_land(ax)
    vmax = max(float(np.nanpercentile(vals, 97)) if vals.size else 1.0, 1e-3)
    sizes = 30 + 260 * np.clip(vals / vmax, 0, 1)
    sc = ax.scatter(lons, lats, c=np.clip(vals, 1e-4, None), s=sizes,
                    cmap=CMAP_BEACH, norm=LogNorm(vmin=max(vmax / 300, 1e-4),
                                                  vmax=max(vmax, 1e-3)),
                    zorder=5, edgecolor=INK["surface"], linewidth=0.9)
    cb = fig.colorbar(sc, ax=ax, pad=0.02, fraction=0.03)
    cb.set_label("Predicted stranding, kg per m of shoreline", fontsize=9,
                 color=INK["secondary"])
    cb.ax.tick_params(colors=INK["muted"], labelsize=8)

    _label_top(ax, lons, lats, vals, [s.name for s in segs], "kg/m")
    horizon_d = res.daily_kg.shape[1]
    _style(ax, f"Predicted shoreline accumulation — next {horizon_d} days",
           f"Issued {res.issued_at:%Y-%m-%d %H:%MZ} · drift from HF-radar "
           f"currents ({res.current_time}) + WRF winds")
    _set_extent(ax)
    fig.tight_layout()
    fig.savefig(out, facecolor=INK["surface"], bbox_inches="tight")
    plt.close(fig)
    return out


def map_emissions(res, h2s_kg_day_km: np.ndarray, cfg, out: Path) -> Path:
    segs = res.segments
    lons = np.array([s.lon for s in segs])
    lats = np.array([s.lat for s in segs])
    tiers = [emis_mod.risk_tier(float(v), cfg) for v in h2s_kg_day_km]
    colors = [STATUS[t] for t in tiers]
    sizes = [40 + 200 * min(v / max(np.nanmax(h2s_kg_day_km), 1e-6), 1)
             for v in h2s_kg_day_km]

    fig, ax = plt.subplots(figsize=(12, 5.2), dpi=140,
                           facecolor=INK["surface"])
    _draw_land(ax)
    ax.scatter(lons, lats, c=colors, s=sizes, zorder=5,
               edgecolor=INK["surface"], linewidth=0.9)
    handles = [plt.Line2D([], [], marker="o", linestyle="", markersize=8,
                          markerfacecolor=STATUS[k], markeredgecolor="none",
                          label=k.capitalize())
               for k in ("minimal", "low", "moderate", "high")]
    leg = ax.legend(handles=handles, title="H₂S emission tier", loc="lower left",
                    frameon=True, fontsize=8, title_fontsize=8.5)
    leg.get_frame().set_edgecolor(INK["axis"])
    leg.get_frame().set_facecolor(INK["surface"])

    _label_top(ax, lons, lats, np.asarray(h2s_kg_day_km),
               [s.name for s in segs], "kg/day/km")
    _style(ax, "Predicted hydrogen sulfide release from stranded Sargassum",
           "Peak daily H₂S per km of shoreline over the forecast window · "
           "first-order estimate, see model notes")
    _set_extent(ax)
    fig.tight_layout()
    fig.savefig(out, facecolor=INK["surface"], bbox_inches="tight")
    plt.close(fig)
    return out


# ------------------------------------------------------------------- web data
M_PER_DEG_LAT = 111_320.0


def segment_line(s) -> List[List[float]]:
    """The shoreline a segment actually covers, as [[lon, lat], ...].

    Segments are stored as a centre point plus a unit *seaward* normal. The
    along-shore direction is that normal rotated 90 degrees, so a segment of
    length L spans centre +/- L/2 along it. Drawing this instead of the centre
    point is what lets the map read as "these beaches" rather than as a
    scatter of pins.
    """
    half = float(s.length_m) / 2.0
    # perpendicular of the seaward normal, in (north, east) metric components
    a_north = -float(s.normal_lon)
    a_east = float(s.normal_lat)
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(float(s.lat)))
    m_per_deg_lon = m_per_deg_lon if m_per_deg_lon > 1.0 else 1.0
    dlat = (a_north * half) / M_PER_DEG_LAT
    dlon = (a_east * half) / m_per_deg_lon
    return [[round(float(s.lon) - dlon, 6), round(float(s.lat) - dlat, 6)],
            [round(float(s.lon) + dlon, 6), round(float(s.lat) + dlat, 6)]]


def write_web_outputs(res, cfg, calib: Dict, web_dir: Path,
                      insitu: pd.DataFrame | None = None,
                      model_meta: Dict | None = None) -> Dict[str, Path]:
    web_dir.mkdir(parents=True, exist_ok=True)
    segs = res.segments
    dt_h = float(cfg.get_path("run.dt_hours", 1))
    per_day = max(1, int(round(24.0 / dt_h)))

    # emission arrays are kg/h at each step; kg per step = kg/h * dt_h
    h2s_kg_day = _daily(res.emission.h2s_kg_per_h * dt_h, per_day)
    nh3_kg_day = _daily(res.emission.nh3_kg_per_h * dt_h, per_day)
    h2s_peak_per_km = h2s_kg_day.max(axis=1) / (
        np.array([s.length_m for s in segs]) / 1000.0)
    h2s_ppm_peak = res.emission.h2s_ppm.max(axis=1)

    days = [(res.issued_at + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(res.daily_kg.shape[1])]
    gas_days = [(res.issued_at + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
                for i in range(h2s_kg_day.shape[1])]

    feats = []
    for i, s in enumerate(segs):
        tier = emis_mod.risk_tier(float(h2s_peak_per_km[i]), cfg)
        feats.append({
            "type": "Feature",
            "geometry": {"type": "LineString",
                         "coordinates": segment_line(s)},
            "properties": {
                "seg_id": s.seg_id, "name": s.name, "island": s.island,
                "coast": s.coast, "length_m": s.length_m,
                "lat": round(float(s.lat), 6), "lon": round(float(s.lon), 6),
                "kg_per_m_total": round(float(calib["kg_per_m"][i]), 3),
                "kg_per_m_physical": round(float(calib["physical_kg_per_m"][i]), 3),
                "tonnes_total": round(float(res.daily_kg[i].sum() / 1000.0), 3),
                "tonnes_by_day": [round(float(x / 1000.0), 3)
                                  for x in res.daily_kg[i]],
                "h2s_kg_per_day": [round(float(x), 3) for x in h2s_kg_day[i]],
                "nh3_kg_per_day": [round(float(x), 3) for x in nh3_kg_day[i]],
                "h2s_peak_kg_day_km": round(float(h2s_peak_per_km[i]), 3),
                "risk_tier": tier,
                "exposure": emis_mod.exposure_context(float(h2s_ppm_peak[i])),
                "source": str(calib["source"][i]),
            },
        })
    seg_gj = {"type": "FeatureCollection", "features": feats,
              "properties": {"days": days, "gas_days": gas_days}}
    paths = {}
    paths["segments"] = web_dir / "forecast_segments.geojson"
    paths["segments"].write_text(json.dumps(seg_gj))

    paths["tracks"] = web_dir / "drift_tracks.json"
    paths["tracks"].write_text(json.dumps({"issued_at": res.issued_at.isoformat(),
                                           "tracks": res.tracks}))

    bio = res.biomass_map
    paths["biomass"] = web_dir / "biomass_field.json"
    paths["biomass"].write_text(json.dumps(_biomass_payload(bio)))

    if insitu is not None and not insitu.empty:
        latest = (insitu.sort_values("time")
                  .groupby("station").tail(1)
                  .to_dict(orient="records"))
        for r in latest:
            r["time"] = pd.Timestamp(r["time"]).isoformat()
        paths["stations"] = web_dir / "stations.json"
        paths["stations"].write_text(json.dumps(latest, default=float))

    total_t = float(res.daily_kg.sum() / 1000.0)
    summary = {
        "issued_at": res.issued_at.isoformat(),
        "afai_scene_time": str(res.afai_time),
        "current_field_time": str(res.current_time),
        "horizon_hours": int(cfg.get_path("run.horizon_hours", 120)),
        "days": days,
        "gas_days": gas_days,
        "offshore_wet_tonnes": round(res.total_offshore_tonnes, 1),
        "predicted_stranding_tonnes": round(total_t, 2),
        "predicted_h2s_tonnes": round(float(h2s_kg_day.sum() / 1000.0), 4),
        "predicted_nh3_tonnes": round(float(nh3_kg_day.sum() / 1000.0), 4),
        "worst_segments": [
            {"name": segs[i].name, "seg_id": segs[i].seg_id,
             "kg_per_m": round(float(calib["kg_per_m"][i]), 2),
             "risk_tier": emis_mod.risk_tier(float(h2s_peak_per_km[i]), cfg)}
            for i in np.argsort(calib["kg_per_m"])[::-1][:10]
        ],
        "calibration_scale": round(float(calib.get("scale", 1.0)), 4),
        "h2s_kg_per_tonne_wet": cfg.get_path("emissions.h2s_kg_per_tonne_wet"),
        "nh3_kg_per_tonne_wet": cfg.get_path("emissions.nh3_kg_per_tonne_wet"),
        "flux_above_literature_fraction": round(
            float(res.emission.flux_above_literature), 3),
        "model": model_meta or {},
        # The bands the website groups segments into. Served from config so the
        # legend and the map can never drift apart, and so the thresholds stay
        # tunable in one place.
        "stranding_classes": cfg.get_path("web.stranding_classes", []),
        "h2s_risk_tiers": cfg.get_path("emissions.risk_tiers", {}),
        "notes": res.notes,
        # Machine-readable input health. The site raises its alert banner off
        # this rather than pattern-matching the prose above.
        "status": getattr(res, "status", {}) or {},
        "maps": {
            "offshore": "maps/offshore_biomass.png",
            "beaching": "maps/beaching_forecast.png",
            "emissions": "maps/h2s_forecast.png",
        },
    }
    paths["summary"] = web_dir / "latest.json"
    paths["summary"].write_text(json.dumps(summary, indent=1))
    return paths


def _daily(arr: np.ndarray, per_day: int) -> np.ndarray:
    n = arr.shape[1]
    n_days = int(np.ceil(n / per_day))
    pad = n_days * per_day - n
    m = np.pad(arr, ((0, 0), (0, pad)))
    return m.reshape(arr.shape[0], n_days, per_day).sum(axis=2)


def _biomass_payload(bio, max_points: int = 9000) -> Dict:
    """Sparse list of Sargassum-bearing cells for the web map."""
    lat = np.asarray(bio["latitude"].values)
    lon = np.asarray(bio["longitude"].values)
    dens = np.asarray(bio["wet_kg_m2"].values, dtype=float)
    ii, jj = np.where(np.isfinite(dens) & (dens > 0))
    if ii.size > max_points:
        keep = np.argsort(dens[ii, jj])[::-1][:max_points]
        ii, jj = ii[keep], jj[keep]
    return {
        "time": str(bio.attrs.get("time")),
        "total_wet_tonnes": round(float(bio.attrs.get("total_wet_tonnes", 0)), 1),
        "bounds": [float(lat.min()), float(lon.min()),
                   float(lat.max()), float(lon.max())],
        "points": [[round(float(lat[i]), 4), round(float(lon[j]), 4),
                    round(float(dens[i, j]), 4)] for i, j in zip(ii, jj)],
    }
