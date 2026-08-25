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
    "feature_archive": ROOT / "data/archive/features.csv",
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
