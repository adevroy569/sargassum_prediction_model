"""The JSON/GeoJSON contract the website consumes."""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

from . import emissions as emis_mod

log = logging.getLogger(__name__)


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
        # Every coefficient the site prints in its published equations. Served
        # from the same config the run used, so the formulas on the page cannot
        # quietly drift away from the ones the model actually applied.
        "coefficients": {
            "windage": cfg.get_path("drift.windage", 0.012),
            "horizontal_diffusivity": cfg.get_path(
                "drift.horizontal_diffusivity", 30.0),
            "capture_km": cfg.get_path("beaching.capture_km", 2.0),
            "onshore_efficiency": cfg.get_path(
                "beaching.onshore_efficiency", 0.35),
            "min_onshore_speed": cfg.get_path(
                "beaching.min_onshore_speed", 0.02),
            "segment_spacing_km": cfg.get_path(
                "beaching.segment_spacing_km", 5.0),
            "onset_lag_hours": cfg.get_path("emissions.onset_lag_hours", 48),
            "peak_hours": cfg.get_path("emissions.peak_hours", 72),
            "decay_efold_hours": cfg.get_path(
                "emissions.decay_efold_hours", 120),
            "deposit_width_m": cfg.get_path("emissions.deposit_width_m", 8.0),
            "mixing_height_m": cfg.get_path("emissions.mixing_height_m", 2.5),
        },
        "notes": res.notes,
        # Machine-readable input health. The site raises its alert banner off
        # this rather than pattern-matching the prose above.
        "status": getattr(res, "status", {}) or {},
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
