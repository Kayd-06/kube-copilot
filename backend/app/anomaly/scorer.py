"""Load the trained Isolation Forest and score a window of pod metrics."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
import joblib
import numpy as np

from app.config import Settings, get_settings
from ml.features import (
    FEATURE_NAMES,
    MIN_WINDOW_POINTS,
    MODEL_FEATURE_NAMES,
    MetricPoint,
    contributions,
    extract_features,
    select_model_features,
)

logger = logging.getLogger(__name__)


class AnomalyModelMissingError(Exception):
    """Raised when the configured model artifact cannot be loaded."""


@dataclass
class ScoreResult:
    namespace: str
    name: str
    score: float
    is_anomaly: bool
    threshold: float
    raw_score: float
    contributing_features: list[dict] = field(default_factory=list)
    features: dict[str, float] = field(default_factory=dict)
    points: list[MetricPoint] = field(default_factory=list)
    warning: str | None = None

    @property
    def insufficient_data(self) -> bool:
        return self.warning is not None and "insufficient" in self.warning


class AnomalyScorer:
    def __init__(self, settings: Settings | None = None, artifact: dict | None = None) -> None:
        self._settings = settings or get_settings()
        self._artifact = artifact
        self._load_error: str | None = None
        if artifact is None:
            self._try_load()

    def _try_load(self) -> None:
        path = self._settings.resolved_anomaly_model_path()
        if not path.is_file():
            self._load_error = (
                f"Anomaly model not found at {path}. Run `make train` from the "
                "repo root, or set ANOMALY_MODEL_PATH to a trained joblib file."
            )
            logger.warning(self._load_error)
            return
        try:
            self._artifact = joblib.load(path)
        except Exception as exc:  # noqa: BLE001
            self._load_error = f"Failed to load anomaly model at {path}: {exc}"
            logger.warning(self._load_error)

    @property
    def loaded(self) -> bool:
        return self._artifact is not None and "model" in self._artifact

    @property
    def missing_message(self) -> str:
        return self._load_error or "Anomaly model is not loaded."

    def require_loaded(self) -> dict:
        if not self.loaded:
            raise AnomalyModelMissingError(self.missing_message)
        assert self._artifact is not None
        return self._artifact

    def score_window(
        self,
        namespace: str,
        name: str,
        points: list[MetricPoint],
    ) -> ScoreResult:
        artifact = self.require_loaded()
        if len(points) < MIN_WINDOW_POINTS:
            return ScoreResult(
                namespace=namespace,
                name=name,
                score=0.0,
                is_anomaly=False,
                threshold=float(artifact["threshold"]),
                raw_score=0.0,
                points=points,
                warning=(
                    f"Insufficient metric history for {namespace}/{name} "
                    f"({len(points)} sample(s); need at least {MIN_WINDOW_POINTS})."
                ),
            )

        full = extract_features(points)
        model_x = select_model_features(full.reshape(1, -1))
        scaler = artifact["scaler"]
        scaled = scaler.transform(model_x)
        raw = float(-artifact["model"].decision_function(scaled)[0])
        threshold = float(
            self._settings.anomaly_score_threshold
            if self._settings.anomaly_score_threshold is not None
            else artifact["threshold"]
        )
        mean = artifact.get("train_raw_mean", threshold)
        std = artifact.get("train_raw_std") or 1.0
        # Sigmoid centred on the threshold so 0.5 == the decision boundary.
        spread = max(std, 1e-6)
        score = float(1.0 / (1.0 + math.exp(-(raw - threshold) / spread)))
        score = min(max(score, 0.0), 1.0)

        full_scaler = artifact.get("full_scaler")
        if full_scaler is not None:
            mean_vec = np.asarray(full_scaler.mean_, dtype=np.float64)
            scale_vec = np.asarray(full_scaler.scale_, dtype=np.float64)
        else:
            # Older artifacts (or tests) may only have the model-feature scaler.
            mean_vec = np.zeros(len(FEATURE_NAMES))
            scale_vec = np.ones(len(FEATURE_NAMES))
            names = list(artifact.get("model_feature_names") or MODEL_FEATURE_NAMES)
            for i, feat_name in enumerate(names):
                try:
                    idx = FEATURE_NAMES.index(feat_name)
                except ValueError:
                    continue
                if i < len(scaler.mean_):
                    mean_vec[idx] = scaler.mean_[i]
                    scale_vec[idx] = scaler.scale_[i]
        contrib = contributions(full, mean_vec, scale_vec, top_k=5)

        return ScoreResult(
            namespace=namespace,
            name=name,
            score=score,
            is_anomaly=raw >= threshold,
            threshold=threshold,
            raw_score=raw,
            contributing_features=contrib,
            features={name: float(value) for name, value in zip(FEATURE_NAMES, full, strict=True)},
            points=points,
        )
