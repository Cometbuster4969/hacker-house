"""Transaction features — every feature is causal (uses only rows before the transaction,
and closed-case labels only after the case was *closed*).

Behavioural identity: a dataset "customer" is derived from the issuer field and pools
many real cardholders, so per-customer baselines are noisy. We add the standard
IEEE-CIS client key (card + billing region + account-open day = day - D1) as ``uid``.
It is a graph vertex in its own right (``Cardholder``) and the unit of case memory:
"this cardholder had confirmed fraud before" is the strongest single signal we have.
"""
from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

CAT_COLS = ["ProductCD", "channel", "P_emaildomain", "R_emaildomain", "id_15", "id_23", "id_34",
            "DeviceType", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "addr2"]
BASE_NUM = ["TransactionAmt", "risk_score", "dist1", "dist2"] + [f"C{i}" for i in range(1, 15)] + \
           [f"D{i}" for i in range(1, 16)] + ["cents", "hour", "addr1n"]


def label_table(closed: pd.DataFrame, opened_before=None) -> pd.DataFrame:
    f = closed[closed.outcome == "confirmed_fraud"]
    if opened_before is not None:
        f = f[f.opened_at < opened_before]
    rows = [(t, r.closed_at, r.case_id, r.pattern) for r in f.itertuples() for t in r.txn_ids.split("|")]
    return pd.DataFrame(rows, columns=["TransactionID", "closed_at", "label_case", "label_pattern"])


def _prior_known(df: pd.DataFrame, key: str) -> np.ndarray:
    ev = df.loc[df.y == 1, [key, "closed_at"]].dropna().sort_values("closed_at")
    if ev.empty:
        return np.zeros(len(df))
    ev["cnt"] = ev.groupby(key).cumcount() + 1
    left = df[[key, "ts"]].reset_index().sort_values("ts")
    m = pd.merge_asof(left, ev.rename(columns={"closed_at": "ts2"})[[key, "ts2", "cnt"]],
                      left_on="ts", right_on="ts2", by=key, direction="backward",
                      allow_exact_matches=False)
    return m.set_index("index").cnt.reindex(df.index).fillna(0).values


def _rolling_count(df: pd.DataFrame, key: str, win: str, values: str | None = None) -> pd.Series:
    def f(d):
        v = d[values].values if values else np.ones(len(d))
        s = pd.Series(v, index=d.ts).rolling(win).sum().values
        return pd.Series(s if values else s - 1, index=d.index)
    return df.groupby(key, group_keys=False).apply(f)


def build_features(df: pd.DataFrame, closed: pd.DataFrame, opened_before=None) -> pd.DataFrame:
    t = time.time()
    df = df.drop(columns=[c for c in ("closed_at", "label_case", "label_pattern", "y") if c in df.columns])
    lab = label_table(closed, opened_before)
    df = df.merge(lab, on="TransactionID", how="left")
    df["y"] = df.closed_at.notna().astype(int)
    df = df.sort_values("ts").reset_index(drop=True)
    df["day"] = df.TransactionDT // 86400
    df["uid"] = df.card_id + "_" + df.addr1.fillna("na").astype(str) + "_" + \
        (df.day - df.D1.fillna(-999)).astype(int).astype(str)
    df["uid2"] = df.uid + "_" + df.P_emaildomain.fillna("na")
    for k in ("uid", "uid2", "card_id", "customer_id"):
        df["known_fraud_" + k] = _prior_known(df, k)
    for k in ("uid", "uid2", "customer_id"):
        g = df.groupby(k)
        df["n_prev_" + k] = g.cumcount()
        cs = g.TransactionAmt.cumsum() - df.TransactionAmt
        df["amt_ratio_" + k] = df.TransactionAmt / (cs / df["n_prev_" + k]).replace(0, np.nan)
        df["dt_prev_" + k] = g.ts.diff().dt.total_seconds()
    df = df.sort_values(["customer_id", "ts"])
    df["small_online"] = ((df.TransactionAmt < 5) & (df.channel == "online")).astype(int)
    df["vel1h"] = _rolling_count(df, "customer_id", "1h")
    df["vel24h"] = _rolling_count(df, "customer_id", "24h")
    df["small1h"] = _rolling_count(df, "customer_id", "1h", "small_online")
    for col in ("ProductCD", "device_profile_id", "addr1", "P_emaildomain"):
        k = df.customer_id + "|" + df[col].fillna("NA").astype(str)
        df["seen_" + col] = (df.groupby(k).cumcount() > 0).astype(int)
    dv = df[df.device_profile_id.notna()].sort_values("ts")
    df["dev_txn7d"] = _rolling_count(dv, "device_profile_id", "7D").reindex(df.index).fillna(0) + \
        df.device_profile_id.notna().astype(int)
    df["dev_specific"] = (df.device_profile_id.notna() &
                          ~df.device_profile_id.fillna("").str.contains("UNK")).astype(int)
    df["cents"] = (df.TransactionAmt * 100 % 100).round()
    df["hour"] = df.ts.dt.hour
    df["addr1n"] = pd.to_numeric(df.addr1, errors="coerce")
    df = df.sort_values("ts").reset_index(drop=True)
    log.info("features built in %.1fs", time.time() - t)
    return df


def model_columns(df: pd.DataFrame) -> list[str]:
    num = BASE_NUM + [c for c in df.columns if c.startswith(
        ("known_fraud", "n_prev", "amt_ratio", "dt_prev", "vel", "small1h", "seen_", "dev_"))]
    return CAT_COLS + num


def prepare_matrix(df: pd.DataFrame, categories: dict | None = None):
    X = df[model_columns(df)].copy()
    cats = {}
    for c in CAT_COLS:
        v = X[c].astype(object).where(X[c].notna(), "NA").astype(str)
        if categories and c in categories:
            X[c] = pd.Categorical(v, categories=categories[c])
        else:
            X[c] = v.astype("category")
        cats[c] = list(X[c].cat.categories)
    return X, cats
