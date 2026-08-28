"""Configuration loading and project paths."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(os.environ.get("SARGASSUM_CONFIG", ROOT / "config/config.yaml"))

PATHS = {
    "root": ROOT,
    "raw": ROOT / "data/raw",
    "archive": ROOT / "data/archive",
    "static": ROOT / "data/static",
    "models": ROOT / "data/models",
    # published under site/ so GitHub Pages can serve the repo's site folder
    "maps": ROOT / "site/maps",
    "web": ROOT / "site/data",
    "cache": ROOT / "data/raw/.cache",
    "segments": ROOT / "data/static/coast_segments.geojson",
    "obs_archive": ROOT / "data/archive/insitu_observations.csv",
    # Catchment biomass indices per station per scene. Written by both
    # backfill.py and every scheduled run, and read back at prediction time to
    # build the 1-3 week lag features. Those three used to disagree: the
    # scheduled run appended to a separate features.csv that nothing ever read,
    # so the lag features depended entirely on the weekly backfill and the
    # per-run rows were thrown away. One path, so they cannot drift again.
    "catchment_history": ROOT / "data/archive/catchment_index_history.csv",
    # Superseded by catchment_history; kept only so an existing checkout can
    # fold its rows in once. See _migrate_feature_archive in update.py.
    "legacy_feature_archive": ROOT / "data/archive/features.csv",
    "forecast_archive": ROOT / "data/archive/forecast_history.csv",
    "model_file": ROOT / "data/models/beaching_model.joblib",
}


class Config(dict):
    """dict with dotted lookup: cfg.get_path('drift.windage')."""

    def get_path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


_cache: Dict[str, Config] = {}


def load(path: Path | str | None = None) -> Config:
    p = str(path or CONFIG_PATH)
    if p not in _cache:
        with open(p) as fh:
            _cache[p] = Config(yaml.safe_load(fh))
    return _cache[p]


def ensure_dirs() -> None:
    for key in ("raw", "archive", "static", "models", "maps", "web", "cache"):
        PATHS[key].mkdir(parents=True, exist_ok=True)
