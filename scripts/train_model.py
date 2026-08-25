#!/usr/bin/env python3
"""Train (or retrain) the learned calibration layer.

Inputs
------
  data/archive/training_table.csv   built by scripts/backfill.py and extended
                                    by every scheduled run
  data/archive/forecast_history.csv physical-model forecasts, used to fit the
                                    single physical bias scale
  data/archive/insitu_observations.csv

Output
------
  data/models/beaching_model.joblib (+ .json with metrics)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from sargassum import config as cfg_mod  # noqa: E402
from sargassum.coastline import load_segments  # noqa: E402
from sargassum.model import BeachingModel  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
log = logging.getLogger("train")

TRAINING_TABLE = cfg_mod.PATHS["archive"] / "training_table.csv"


def nearest_segment(segs, lat, lon):
    d = [(s.lat - lat) ** 2 + (s.lon - lon) ** 2 for s in segs]
    return segs[int(np.argmin(d))].seg_id


def fit_physical_scale(cfg, model: BeachingModel) -> float:
    """Compare past physical forecasts with what the traps later measured."""
    fc_path = cfg_mod.PATHS["forecast_archive"]
    obs_path = cfg_mod.PATHS["obs_archive"]
    if not (fc_path.exists() and obs_path.exists()):
        log.info("no forecast/observation overlap yet; physical scale = 1.0")
        return 1.0
    fc = pd.read_csv(fc_path)
    obs = pd.read_csv(obs_path)
    if fc.empty or obs.empty:
        return 1.0
    fc["issued_at"] = pd.to_datetime(fc["issued_at"], utc=True)
    obs["time"] = pd.to_datetime(obs["time"], utc=True)

    segs = load_segments(cfg_mod.PATHS["segments"])
    obs["seg_id"] = [nearest_segment(segs, r.latitude, r.longitude)
                     for r in obs.itertuples()]

    pairs = []
    for seg_id, g in obs.groupby("seg_id"):
        f = fc[fc["seg_id"] == seg_id].sort_values("issued_at")
        if f.empty:
            continue
        merged = pd.merge_asof(
            g.sort_values("time"), f, left_on="time", right_on="issued_at",
            direction="backward", tolerance=pd.Timedelta(days=7))
        merged = merged.dropna(subset=["kg_per_m_physical"])
        pairs.append(merged[["biomass_kg_per_m", "kg_per_m_physical"]])
    if not pairs:
        return 1.0
    allp = pd.concat(pairs)
    return model.fit_physical_scale(allp["biomass_kg_per_m"].to_numpy(),
                                    allp["kg_per_m_physical"].to_numpy())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default=str(TRAINING_TABLE))
    ap.add_argument("--allow-empty", action="store_true",
                    help="exit 0 instead of failing when there is no data yet")
    args = ap.parse_args()

    cfg = cfg_mod.load()
    cfg_mod.ensure_dirs()
    path = Path(args.table)
    if not path.exists():
        msg = f"{path} not found - run scripts/backfill.py first"
        if args.allow_empty:
            log.warning(msg)
            return 0
        log.error(msg)
        return 1

    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    model = BeachingModel(cfg)
    try:
        metrics = model.fit(df)
    except ValueError as exc:
        if args.allow_empty:
            log.warning("skipping training: %s", exc)
            return 0
        log.error("training failed: %s", exc)
        return 1

    fit_physical_scale(cfg, model)
    model.save(cfg_mod.PATHS["model_file"])
    log.info("saved model to %s", cfg_mod.PATHS["model_file"])
    log.info("hold-out MAE %.3f kg/m (log-space MAE %.3f, R2 %.3f) on %d rows",
             metrics.mae_kg_per_m, metrics.mae_log, metrics.r2_log,
             metrics.n_test)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
