#!/usr/bin/env python3
"""Main scheduled job: fetch -> forecast -> render -> archive.

Run from the repository root:

    python scripts/update.py
    python scripts/update.py --offline     # synthetic data, no network
"""
from __future__ import annotations

import argparse
import logging
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from sargassum import config as cfg_mod  # noqa: E402
from sargassum import features, pipeline, render, sources  # noqa: E402
from sargassum.model import BeachingModel  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
log = logging.getLogger("update")


def append_archive(path: Path, rows: pd.DataFrame, key_cols,
                   max_rows: int = 0) -> None:
    """Append rows to a CSV archive, de-duplicating on `key_cols`."""
    if rows is None or rows.empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = pd.read_csv(path)
        rows = pd.concat([old, rows], ignore_index=True)
    rows = rows.drop_duplicates(subset=list(key_cols), keep="last")
    for col in ("time", "issued_at"):
        if col in rows.columns:
            rows = rows.sort_values(col)
            break
    if max_rows and len(rows) > max_rows:
        rows = rows.tail(max_rows)
    rows.to_csv(path, index=False)
    log.info("archive %s -> %d rows", path.name, len(rows))


def segments_near_stations(segments, insitu: pd.DataFrame,
                           radius_km: float = 25.0):
    """Only these segments are worth archiving forecasts for - they are the
    ones that can ever be compared against a measurement."""
    if insitu is None or insitu.empty:
        return set()
    pts = insitu[["latitude", "longitude"]].dropna().drop_duplicates()
    keep = set()
    for s in segments:
        d = np.min(np.hypot((pts["latitude"] - s.lat) * 111.32,
                            (pts["longitude"] - s.lon) * 105.8))
        if d <= radius_km:
            keep.add(s.seg_id)
    return keep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true",
                    help="run against synthetic fields (CI smoke test)")
    ap.add_argument("--no-archive", action="store_true")
    args = ap.parse_args()

    cfg = cfg_mod.load()
    cfg_mod.ensure_dirs()

    bundle = None
    if args.offline:
        from tests.synthetic import synthetic_bundle
        bundle = synthetic_bundle(cfg)

    res = pipeline.run(cfg, offline_bundle=bundle)

    insitu = pd.DataFrame()
    if not args.offline:
        try:
            insitu = sources.fetch_insitu(cfg, cache_dir=cfg_mod.PATHS["cache"])
        except Exception as exc:  # noqa: BLE001
            log.warning("in-situ fetch failed: %s", exc)

    model = BeachingModel.load(cfg_mod.PATHS["model_file"], cfg)
    calib = pipeline.apply_calibration(res, cfg, model, insitu)

    # ------------------------------------------------------------- outputs
    maps = cfg_mod.PATHS["maps"]
    render.map_offshore_biomass(res, maps / "offshore_biomass.png")
    render.map_beaching(res, calib["kg_per_m"], maps / "beaching_forecast.png")
    dt_h = float(cfg.get_path("run.dt_hours", 1))
    per_day = max(1, int(round(24.0 / dt_h)))
    h2s_daily = render._daily(res.emission.h2s_kg_per_h * dt_h, per_day)
    length_km = np.array([s.length_m for s in res.segments]) / 1000.0
    h2s_peak_km = h2s_daily.max(axis=1) / length_km
    render.map_emissions(res, h2s_peak_km, cfg, maps / "h2s_forecast.png")

    meta = {}
    if model and model.metrics:
        meta = model.metrics.to_dict()
    render.write_web_outputs(res, cfg, calib, cfg_mod.PATHS["web"],
                             insitu=insitu, model_meta=meta)

    # ------------------------------------------------------------- archive
    if not args.no_archive and not args.offline:
        if not insitu.empty:
            obs = insitu.copy()
            obs["time"] = pd.to_datetime(obs["time"], utc=True).astype(str)
            append_archive(cfg_mod.PATHS["obs_archive"], obs,
                           ["station", "time"])
        try:
            rows = []
            stations = (insitu.groupby("station")[["latitude", "longitude"]]
                        .last().reset_index()) if not insitu.empty else \
                pd.DataFrame(columns=["station", "latitude", "longitude"])
            bio_seed = res.biomass_seed
            for _, st in stations.iterrows():
                idx = features.catchment_indices(bio_seed, float(st.latitude),
                                                 float(st.longitude), cfg)
                idx.update({"station": st.station,
                            "time": str(res.afai_time),
                            "latitude": float(st.latitude),
                            "longitude": float(st.longitude)})
                rows.append(idx)
            if rows:
                append_archive(cfg_mod.PATHS["feature_archive"],
                               pd.DataFrame(rows), ["station", "time"])
        except Exception:  # noqa: BLE001
            log.warning("feature archiving failed:\n%s", traceback.format_exc())

        # Archive forecasts only where a trap can later verify them, so the
        # committed history stays small enough to live in git.
        keep = segments_near_stations(res.segments, insitu)
        if keep:
            mask = np.array([s.seg_id in keep for s in res.segments])
            fc = pd.DataFrame({
                "issued_at": str(res.issued_at),
                "seg_id": [s.seg_id for s, m in zip(res.segments, mask) if m],
                "kg_per_m": calib["kg_per_m"][mask],
                "kg_per_m_physical": calib["physical_kg_per_m"][mask],
                "h2s_kg_day_km": h2s_peak_km[mask],
            })
            append_archive(cfg_mod.PATHS["forecast_archive"], fc,
                           ["issued_at", "seg_id"],
                           max_rows=int(cfg.get_path("run.archive_max_rows",
                                                     400000)))

    log.info("done: %.1f t predicted stranding over the horizon",
             res.daily_kg.sum() / 1000.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
