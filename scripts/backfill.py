#!/usr/bin/env python3
"""Build the historical training table from AFAI history + in-situ records.

Downloads one AFAI composite per observation week (2020 -> now), converts it
to a wet-biomass field, and integrates it over nested upstream catchments
around every CariCOOS Sargassum trap station. The result is joined to the
weekly in-situ measurements to give a supervised training set on day one
instead of waiting months for the live archive to fill.

The job is resumable: every downloaded scene is cached on disk, so re-running
after an interruption only fetches what is missing.

    python scripts/backfill.py --start 2020-10-01
    python scripts/backfill.py --start 2024-01-01 --limit 40   # quick test
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from sargassum import biomass as bio_mod  # noqa: E402
from sargassum import config as cfg_mod  # noqa: E402
from sargassum import erddap, features, sources  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
log = logging.getLogger("backfill")

TRAINING_TABLE = cfg_mod.PATHS["archive"] / "training_table.csv"
INDEX_CACHE = cfg_mod.PATHS["catchment_history"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2020-10-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--product", default=None, choices=["7d", "3d", "1d"],
                    help="AFAI composite to use (default: "
                         "sources.afai.backfill_product from config)")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N scenes (for smoke tests)")
    ap.add_argument("--sleep", type=float, default=1.5,
                    help="seconds between ERDDAP requests")
    args = ap.parse_args()

    cfg = cfg_mod.load()
    cfg_mod.ensure_dirs()
    cache = cfg_mod.PATHS["cache"]

    # ---------------------------------------------------------- in-situ obs
    log.info("fetching in-situ station history")
    obs = sources.fetch_insitu(cfg, since=f"{args.start}T00:00:00Z",
                               cache_dir=cache)
    if obs.empty:
        log.error("no in-situ observations returned; nothing to train on")
        return 1
    obs["time"] = pd.to_datetime(obs["time"], utc=True)
    if args.end:
        obs = obs[obs["time"] <= pd.Timestamp(args.end, tz="UTC")]
    log.info("%d observations at %d stations, %s -> %s", len(obs),
             obs["station"].nunique(), obs["time"].min(), obs["time"].max())
    obs.to_csv(cfg_mod.PATHS["obs_archive"], index=False)

    stations = (obs.groupby("station")[["latitude", "longitude"]]
                .median().reset_index())

    # scene dates: one per observation week
    weeks = sorted(obs["time"].dt.to_period("W").unique())
    scene_dates = [w.start_time.tz_localize("UTC") + pd.Timedelta(days=3)
                   for w in weeks]
    if args.limit:
        scene_dates = scene_dates[-args.limit:]
    log.info("%d scenes to process", len(scene_dates))

    done = set()
    rows = []
    if INDEX_CACHE.exists():
        prev = pd.read_csv(INDEX_CACHE)
        rows = prev.to_dict("records")
        done = set(pd.to_datetime(prev["time"], utc=True)
                   .dt.strftime("%Y-%m-%d"))
        log.info("resuming; %d scenes already indexed", len(done))

    src = cfg["sources"]["afai"]
    product = args.product or src.get("backfill_product", "3d")
    ds_id = src[f"dataset_{product}"]
    log.info("using AFAI %s composite (%s)", product, ds_id)
    dom = cfg["domains"]["seed"]

    for n, when in enumerate(scene_dates, 1):
        key = when.strftime("%Y-%m-%d")
        if key in done:
            continue
        sel = f"({key}T00:00:00Z)"
        try:
            ds = erddap.griddap(
                server=src["server"], dataset=ds_id,
                variables=[src["variable"]], time_sel=sel,
                lat=(dom["lat_min"], dom["lat_max"]),
                lon=(dom["lon_min"], dom["lon_max"]),
                stride=int(dom.get("stride", 1)),
                cache_dir=cache, cache_ttl_s=365 * 24 * 3600,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("scene %s unavailable: %s", key, exc)
            continue

        bio = bio_mod.biomass_field(ds, cfg, var=src["variable"])
        for _, st in stations.iterrows():
            idx = features.catchment_indices(bio, float(st.latitude),
                                             float(st.longitude), cfg)
            idx.update({"station": st.station, "time": key,
                        "latitude": float(st.latitude),
                        "longitude": float(st.longitude)})
            rows.append(idx)
        if n % 10 == 0 or n == len(scene_dates):
            pd.DataFrame(rows).to_csv(INDEX_CACHE, index=False)
            log.info("%d/%d scenes (%s) total %.0f t in domain",
                     n, len(scene_dates), key,
                     bio.attrs.get("total_wet_tonnes", 0))
        time.sleep(args.sleep)

    idx_df = pd.DataFrame(rows)
    idx_df.to_csv(INDEX_CACHE, index=False)
    log.info("catchment index history: %d rows", len(idx_df))

    # ------------------------------------------------------------- assemble
    lags = list(cfg.get_path("model.lags_weeks", [0, 1, 2, 3]))
    feat = features.assemble(idx_df.to_dict("records"), lags)
    feat["time"] = pd.to_datetime(feat["time"], utc=True)

    obs_r = obs[["time", "station", "biomass_kg_per_m"]].copy()
    merged = pd.merge_asof(
        obs_r.sort_values("time"),
        feat.sort_values("time"),
        on="time", by="station", direction="nearest",
        tolerance=pd.Timedelta(days=6),
    ).dropna(subset=["biomass_t_r100"])

    merged.to_csv(TRAINING_TABLE, index=False)
    log.info("training table: %d rows, %d columns -> %s",
             len(merged), merged.shape[1], TRAINING_TABLE)
    log.info("target summary: median %.2f kg/m, p90 %.2f, max %.2f",
             merged["biomass_kg_per_m"].median(),
             merged["biomass_kg_per_m"].quantile(0.9),
             merged["biomass_kg_per_m"].max())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
