"""End-to-end forecast run."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import biomass as bio_mod
from . import beaching as beach_mod
from . import config as cfg_mod
from . import drift, emissions, features, sources
from . import coastline as coastline_mod
from .coastline import Segment, load_segments, write_segments
from .model import BeachingModel

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    issued_at: pd.Timestamp
    afai_time: Optional[pd.Timestamp]
    afai_product: str
    current_time: Optional[pd.Timestamp]
    segments: List[Segment]
    daily_kg: np.ndarray            # [segment, day] stranded wet mass
    kg_per_m: np.ndarray            # [segment] total over horizon
    emission: emissions.EmissionResult
    biomass_map: object
    biomass_seed: object
    tracks: List[Dict]
    total_offshore_tonnes: float
    notes: List[str]
    # Machine-readable health of each input, so the website can raise a precise
    # alert instead of regex-matching the prose in `notes`.
    status: Dict = field(default_factory=dict)


def _segments(cfg) -> List[Segment]:
    """Cached receptor list, rebuilt whenever the shoreline it came from
    changes.

    The cache used to be keyed on existence alone, so editing the island
    outlines silently had no effect: every later run kept reusing receptors
    derived from the previous shoreline.
    """
    p = cfg_mod.PATHS["segments"]
    spacing = float(cfg.get_path("beaching.segment_spacing_km", 5.0))
    if p.exists():
        if coastline_mod.segments_are_current(p):
            return load_segments(p)
        log.info("shoreline has changed since %s was built; rebuilding "
                 "receptors", p.name)
    return write_segments(p, spacing)


def run(cfg, offline_bundle: Optional[Dict] = None) -> RunResult:
    """Run the full forecast.

    `offline_bundle` lets tests inject synthetic fields instead of hitting the
    network; it must supply keys: afai_seed, afai_map, currents, wind.
    """
    cfg_mod.ensure_dirs()
    cache = cfg_mod.PATHS["cache"]
    notes: List[str] = []
    status: Dict = {"wind": {"ok": False, "reason": "not attempted"}}
    segs = _segments(cfg)

    horizon = int(cfg.get_path("run.horizon_hours", 120))
    dt_h = float(cfg.get_path("run.dt_hours", 1))
    n_steps = int(round(horizon / dt_h))
    issued = pd.Timestamp.utcnow().floor("h")

    # ---------------------------------------------------------- input data
    product = "synthetic"
    if offline_bundle:
        ds_seed = offline_bundle["afai_seed"]
        ds_map = offline_bundle.get("afai_map", ds_seed)
        cur_latest, cur_mean, cur_time = offline_bundle["currents"]
        wind_fields, wind_times = offline_bundle.get("wind", ([], []))
        afai_time = offline_bundle.get("afai_time")
        status["wind"] = {"ok": bool(len(wind_fields)), "dataset": "synthetic",
                          "reason": "" if len(wind_fields) else "none supplied"}
    else:
        product = sources.choose_afai_product(cfg)
        if product != "7d":
            notes.append(f"AFAI {product} composite used (7-day scene stale)")
        ds_seed = sources.fetch_afai(cfg, "seed", product, cache_dir=cache)
        afai_time = pd.Timestamp(np.asarray(ds_seed["time"].values).ravel()[-1])
        try:
            ds_map = sources.fetch_afai(cfg, "map", product, cache_dir=cache)
        except Exception as exc:  # noqa: BLE001
            log.warning("high-res map AFAI failed (%s); reusing seed grid", exc)
            notes.append("map grid fell back to the coarse seed grid")
            ds_map = ds_seed
        cur_latest, cur_mean, cur_time = sources.fetch_currents(cfg, cache)
        try:
            wind_fields, wind_times = sources.fetch_wind(cfg, horizon, cache)
            # A 2 km WRF nest runs shorter than the drift horizon. `wind_at`
            # keeps returning the last field past the end of the run, which is
            # persistence - say so rather than letting it pass as forecast.
            covered_h = float(horizon)
            if len(wind_times):
                covered_h = (pd.DatetimeIndex(wind_times)[-1]
                             - issued).total_seconds() / 3600.0
                if covered_h < horizon - 1:
                    notes.append(
                        f"WRF wind runs {covered_h:.0f} h of the {horizon} h "
                        f"horizon; wind is held constant beyond that")
            status["wind"] = {
                "ok": bool(len(wind_fields)),
                "dataset": cfg["sources"]["wind"]["dataset"],
                "n_fields": int(len(wind_fields)),
                "covered_hours": round(covered_h, 1),
                "horizon_hours": horizon,
                "reason": "",
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("wind fetch failed: %s", exc)
            notes.append(
                "no WRF wind available, so windage is disabled and rafts move "
                "on currents alone. Windage is the main mechanism that drives "
                "Sargassum ashore, so this run understates stranding.")
            wind_fields, wind_times = [], pd.DatetimeIndex([])
            status["wind"] = {"ok": False, "n_fields": 0,
                              "dataset": cfg["sources"]["wind"]["dataset"],
                              "horizon_hours": horizon,
                              "reason": f"{type(exc).__name__}: {exc}"[:400]}

    bio_seed = bio_mod.biomass_field(ds_seed, cfg)
    bio_map = bio_mod.biomass_field(ds_map, cfg)
    total_t = float(bio_seed.attrs.get("total_wet_tonnes", 0.0))
    log.info("offshore biomass in seed domain: %.0f t", total_t)

    # ------------------------------------------------------------- drifting
    rng = np.random.default_rng(int(cfg.get_path("model.random_state", 42)))
    parts = drift.seed_from_field(bio_seed, cfg)
    log.info("seeded %d particles carrying %.0f t", len(parts),
             parts.mass_kg.sum() / 1000 if len(parts) else 0.0)

    acc = beach_mod.BeachingAccumulator(segs, n_steps)
    land = beach_mod.LandMask()
    efold = float(cfg.get_path("drift.persistence_efold_hours", 24.0))
    track_idx = (np.argsort(parts.mass_kg)[::-1][:120] if len(parts) else
                 np.array([], dtype=int))
    tracks = [{"lat": [], "lon": [], "mass_kg": float(parts.mass_kg[i])}
              for i in track_idx]

    for step_i in range(n_steps):
        lead_h = step_i * dt_h
        cur = drift.blended_current(cur_latest, cur_mean, lead_h, efold)
        wf = (sources.wind_at(list(wind_fields), pd.DatetimeIndex(wind_times),
                              issued + pd.Timedelta(hours=lead_h))
              if len(wind_fields) else None)
        u, v = drift.velocity_at(parts, cur, wf, cfg)
        acc.capture(parts, u, v, step_i, dt_h, cfg)
        prev_lat, prev_lon = parts.lat.copy(), parts.lon.copy()
        drift.step(parts, cur, wf, dt_h, cfg, rng)
        acc.block_land(parts, land, prev_lat, prev_lon)
        if step_i % max(1, int(6 / dt_h)) == 0:
            for t, i in zip(tracks, track_idx):
                t["lat"].append(round(float(parts.lat[i]), 4))
                t["lon"].append(round(float(parts.lon[i]), 4))

    stranded = acc.mass
    log.info("stranded over horizon: %.1f t", stranded.sum() / 1000.0)

    # ------------------------------------------------------------ emissions
    tail_h = float(cfg.get_path("emissions.tail_hours", 288))
    n_tail = int(round(tail_h / dt_h))
    stranded_ext = np.pad(stranded, ((0, 0), (0, n_tail)))

    onshore_wind = 4.0
    if len(wind_fields):
        w0 = wind_fields[0]
        lat_s = np.array([s.lat for s in segs])
        lon_s = np.array([s.lon for s in segs])
        uw, vw = w0.sample(lat_s, lon_s)
        onshore_wind = np.maximum(np.hypot(uw, vw), 1.0)
    emis = emissions.emissions(stranded_ext, acc.length_m, dt_h, cfg,
                               onshore_wind_ms=onshore_wind)
    if emis.flux_above_literature > 0.02:
        notes.append(
            f"{emis.flux_above_literature:.0%} of emitting cells imply an "
            "areal H2S flux above the published Martinique range")

    return RunResult(
        issued_at=issued,
        afai_time=afai_time,
        afai_product=product,
        current_time=cur_time,
        segments=segs,
        daily_kg=acc.daily_kg(dt_h),
        kg_per_m=acc.kg_per_m(),
        emission=emis,
        biomass_map=bio_map,
        biomass_seed=bio_seed,
        tracks=[t for t in tracks if t["lat"]],
        total_offshore_tonnes=total_t,
        notes=notes,
        status=status,
    )


# --------------------------------------------------------------- calibration
def _nearest_segment_index(segs: List[Segment], lat: float, lon: float) -> int:
    d = [(s.lat - lat) ** 2 + (s.lon - lon) ** 2 for s in segs]
    return int(np.argmin(d))


def apply_calibration(res: RunResult, cfg, model: Optional[BeachingModel],
                      insitu: Optional[pd.DataFrame] = None
                      ) -> Dict[str, np.ndarray]:
    """Blend the physical forecast with the learned station model.

    Every segment gets `physical x physical_scale`. Segments that host a
    CariCOOS trap - i.e. where the learned model was actually trained - are
    overwritten with the learned prediction and flagged as such, so the
    published data always says which path produced each number.
    """
    phys = res.kg_per_m.copy()
    scale = float(model.physical_scale) if model else 1.0
    out = phys * scale
    source = np.array(["physical"] * len(phys), dtype=object)

    if model is None or model.model is None or insitu is None or insitu.empty:
        return {"physical_kg_per_m": phys, "kg_per_m": out,
                "scale": scale, "source": source}

    try:
        hist_path = cfg_mod.PATHS["archive"] / "catchment_index_history.csv"
        hist = pd.read_csv(hist_path) if hist_path.exists() else pd.DataFrame()
        stations = (insitu.groupby("station")[["latitude", "longitude"]]
                    .median().reset_index())
        now_key = pd.Timestamp(res.afai_time).strftime("%Y-%m-%d")
        rows = []
        for _, st in stations.iterrows():
            idx = features.catchment_indices(res.biomass_seed,
                                             float(st.latitude),
                                             float(st.longitude), cfg)
            idx.update({"station": st.station, "time": now_key,
                        "latitude": float(st.latitude),
                        "longitude": float(st.longitude)})
            rows.append(idx)
        combined = (pd.concat([hist, pd.DataFrame(rows)], ignore_index=True)
                    if not hist.empty else pd.DataFrame(rows))
        combined = combined.drop_duplicates(subset=["station", "time"],
                                            keep="last")
        lags = list(cfg.get_path("model.lags_weeks", [0, 1, 2, 3]))
        feat = features.assemble(combined.to_dict("records"), lags)
        feat["time"] = pd.to_datetime(feat["time"], utc=True)
        latest = feat.sort_values("time").groupby("station").tail(1)
        preds = model.predict(latest)
        for st, pred in zip(latest["station"], preds):
            row = stations[stations["station"] == st]
            if row.empty:
                continue
            i = _nearest_segment_index(res.segments, float(row.latitude.iloc[0]),
                                       float(row.longitude.iloc[0]))
            out[i] = float(pred)
            source[i] = "learned"
        log.info("learned model applied to %d segments", int((source == "learned").sum()))
    except Exception as exc:  # noqa: BLE001 - never let calibration break a run
        log.warning("learned calibration skipped: %s", exc)

    return {"physical_kg_per_m": phys, "kg_per_m": out,
            "scale": scale, "source": source}
