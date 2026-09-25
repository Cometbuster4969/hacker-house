"""Calibrated transaction-level fraud model.

Labels are the bank's own closed investigations (July-October): a transaction is a
positive iff it sits in a ``confirmed_fraud`` case. The only numbers the model sees
are the dataset's columns plus causal behavioural features (see features.py).

The model replaces "trust the risk score" — the README warns the score "is often wrong
in both directions". On an honest time split (train Jul-Sep, test October) the model
reaches ROC-AUC ~0.97 versus 0.87 for the raw risk score (see benchmark/backtest.json).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from . import config
from .features import prepare_matrix

log = logging.getLogger(__name__)

PARAMS = dict(max_iter=400, learning_rate=0.06, max_leaf_nodes=63, l2_regularization=1.0,
              categorical_features="from_dtype", random_state=0)


@dataclass
class TxnModel:
    clf: HistGradientBoostingClassifier
    categories: dict
    columns: list
    train_prevalence: float
    meta: dict

    def score(self, df: pd.DataFrame) -> np.ndarray:
        X, _ = prepare_matrix(df, self.categories)
        return self.clf.predict_proba(X[self.columns])[:, 1]

    def save(self, name: str = "txn_model.joblib"):
        config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, config.MODELS_DIR / name, compress=3)
        (config.MODELS_DIR / (name.replace(".joblib", ".json"))).write_text(json.dumps(self.meta, indent=2))

    @staticmethod
    def load(name: str = "txn_model.joblib") -> "TxnModel":
        return joblib.load(config.MODELS_DIR / name)


def train(df: pd.DataFrame, train_mask, valid_mask=None) -> TxnModel:
    X, cats = prepare_matrix(df)
    cols = list(X.columns)
    clf = HistGradientBoostingClassifier(**PARAMS)
    clf.fit(X.loc[train_mask, cols], df.loc[train_mask, "y"])
    meta = {"n_train": int(train_mask.sum()), "train_prevalence": float(df.loc[train_mask, "y"].mean()),
            "params": {k: str(v) for k, v in PARAMS.items()}, "features": cols}
    if valid_mask is not None and valid_mask.sum():
        p = clf.predict_proba(X.loc[valid_mask, cols])[:, 1]
        y = df.loc[valid_mask, "y"]
        meta["validation"] = {
            "n": int(valid_mask.sum()), "roc_auc": round(float(roc_auc_score(y, p)), 4),
            "avg_precision": round(float(average_precision_score(y, p)), 4),
            "brier": round(float(brier_score_loss(y, p)), 5),
            "risk_score_roc_auc": round(float(roc_auc_score(y, df.loc[valid_mask, "risk_score"])), 4)}
        log.info("validation: %s", meta["validation"])
    return TxnModel(clf, cats, cols, meta["train_prevalence"], meta)


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def sigmoid(x):
    return 1 / (1 + np.exp(-np.asarray(x, dtype=float)))
