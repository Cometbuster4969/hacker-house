"""Case-level probability: from transaction scores to a calibrated case probability.

Fitted on closed cases (confirmed vs cleared) replayed through the same episode logic,
then prior-shifted from the closed-case base rate (84% fraud) to the benchmark's stated
base rate ("Half the cases are legitimate"). Both numbers are reported in the backtest.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression

from .model import logit, sigmoid

CASE_FEATURES = ["lp_flag", "lp_max", "log_n_members", "known_uid"]


def case_vector(p_flag: float, p_max: float, n_members: int, known_uid: float) -> list[float]:
    return [float(logit(p_flag)), float(logit(p_max)), float(np.log1p(n_members)), float(known_uid > 0)]


@dataclass
class CaseCalibrator:
    lr: LogisticRegression
    train_prior: float
    target_prior: float

    def raw(self, x: list[float]) -> float:
        return float(self.lr.predict_proba(np.array([x]))[0, 1])

    def __call__(self, x: list[float]) -> float:
        """Calibrated, prior-shifted probability, clipped to [0.02, 0.98] (never 0 or 1)."""
        z = logit(self.raw(x)) - logit(self.train_prior) + logit(self.target_prior)
        return float(np.clip(sigmoid(z), 0.02, 0.98))


def fit(X: np.ndarray, y: np.ndarray, target_prior: float) -> CaseCalibrator:
    lr = LogisticRegression(C=1.0, max_iter=1000)
    lr.fit(X, y)
    return CaseCalibrator(lr, float(np.mean(y)), target_prior)
