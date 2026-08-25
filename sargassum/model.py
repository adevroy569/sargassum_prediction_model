"""Learned calibration layer.

Two things are learned from the CariCOOS in-situ record:

1. `BeachingModel` - a gradient-boosted regressor mapping satellite-derived
   upstream biomass (nested catchments, weekly lags) + seasonality + site to
   observed influx in kg per metre of shoreline at La Parguera.

2. `physical_scale` - a single multiplicative bias correction between the
   physical drift/beaching model and the same observations, used to calibrate
   segments that have no in-situ instrument.

The learned model is only trusted where it was trained (La Parguera). Every
other coast segment is served by the physical model times `physical_scale`,
and the published output records which path produced each number.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score

from .features import feature_columns

log = logging.getLogger(__name__)


@dataclass
class ModelMetrics:
    n_train: int
    n_test: int
    mae_log: float
    r2_log: float
    mae_kg_per_m: float
    median_obs_kg_per_m: float
    trained_at: str
    features: List[str]

    def to_dict(self) -> Dict:
        return asdict(self)


class BeachingModel:
    def __init__(self, cfg):
        self.cfg = cfg
        self.model: Optional[HistGradientBoostingRegressor] = None
        self.features: List[str] = []
        self.stations: List[str] = []
        self.metrics: Optional[ModelMetrics] = None
        self.physical_scale: float = 1.0

    # ------------------------------------------------------------ training
    def _encode(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if "station" in df.columns:
            if not self.stations:
                self.stations = sorted(df["station"].astype(str).unique())
            codes = {s: i for i, s in enumerate(self.stations)}
            df["station_code"] = df["station"].astype(str).map(codes).fillna(-1)
        return df

    def fit(self, df: pd.DataFrame) -> ModelMetrics:
        min_rows = int(self.cfg.get_path("model.min_training_rows", 60))
        df = df.dropna(subset=["biomass_kg_per_m"])
        if len(df) < min_rows:
            raise ValueError(f"only {len(df)} training rows, need {min_rows}")

        df = self._encode(df).sort_values("time")
        self.features = feature_columns(df)
        X = df[self.features].to_numpy(dtype=float)
        y = np.log1p(np.clip(df["biomass_kg_per_m"].to_numpy(dtype=float), 0, None))

        # chronological hold-out: the last fraction of the record
        frac = float(self.cfg.get_path("model.test_fraction", 0.2))
        split = max(1, int(len(df) * (1 - frac)))
        Xtr, Xte, ytr, yte = X[:split], X[split:], y[:split], y[split:]

        self.model = HistGradientBoostingRegressor(
            loss="squared_error",
            max_iter=400,
            learning_rate=0.05,
            max_leaf_nodes=15,
            min_samples_leaf=8,
            l2_regularization=1.0,
            random_state=int(self.cfg.get_path("model.random_state", 42)),
        )
        self.model.fit(Xtr, ytr)

        if len(yte) >= 5:
            pred = self.model.predict(Xte)
            mae_log = float(mean_absolute_error(yte, pred))
            r2 = float(r2_score(yte, pred))
            mae_kg = float(mean_absolute_error(np.expm1(yte), np.expm1(pred)))
        else:
            mae_log, r2, mae_kg = float("nan"), float("nan"), float("nan")

        self.metrics = ModelMetrics(
            n_train=int(len(ytr)), n_test=int(len(yte)),
            mae_log=mae_log, r2_log=r2, mae_kg_per_m=mae_kg,
            median_obs_kg_per_m=float(np.median(df["biomass_kg_per_m"])),
            trained_at=pd.Timestamp.utcnow().isoformat(),
            features=list(self.features),
        )
        # refit on everything for production use
        self.model.fit(X, y)
        log.info("model trained: %s", self.metrics.to_dict())
        return self.metrics

    def fit_physical_scale(self, obs_kg_per_m: np.ndarray,
                           phys_kg_per_m: np.ndarray) -> float:
        ok = np.isfinite(obs_kg_per_m) & np.isfinite(phys_kg_per_m) & (phys_kg_per_m > 0)
        if ok.sum() >= 10:
            ratio = obs_kg_per_m[ok] / phys_kg_per_m[ok]
            self.physical_scale = float(np.clip(np.median(ratio), 0.02, 50.0))
        log.info("physical bias scale = %.3f", self.physical_scale)
        return self.physical_scale

    # ---------------------------------------------------------- prediction
    def predict(self, df: pd.DataFrame) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("model not trained")
        df = self._encode(df)
        for c in self.features:
            if c not in df.columns:
                df[c] = np.nan
        X = df[self.features].to_numpy(dtype=float)
        return np.clip(np.expm1(self.model.predict(X)), 0.0, None)

    # ------------------------------------------------------------ storage
    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {"model": self.model, "features": self.features,
             "stations": self.stations, "physical_scale": self.physical_scale,
             "metrics": self.metrics.to_dict() if self.metrics else None},
            path,
        )
        Path(str(path) + ".json").write_text(json.dumps(
            {"features": self.features, "stations": self.stations,
             "physical_scale": self.physical_scale,
             "metrics": self.metrics.to_dict() if self.metrics else None},
            indent=2))

    @classmethod
    def load(cls, path: Path, cfg) -> Optional["BeachingModel"]:
        p = Path(path)
        if not p.exists():
            return None
        blob = joblib.load(p)
        obj = cls(cfg)
        obj.model = blob["model"]
        obj.features = blob["features"]
        obj.stations = blob.get("stations", [])
        obj.physical_scale = float(blob.get("physical_scale", 1.0))
        m = blob.get("metrics")
        obj.metrics = ModelMetrics(**m) if m else None
        return obj
