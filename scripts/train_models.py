#!/usr/bin/env python3
"""Train every learned component and write an honest, time-split backtest.

  1. Store + causal features.
  2. BACKTEST arm: labels from cases opened before 2016-09-01 only; transaction model
     trained on July-August; 1,000+ closed cases opened September-October replayed
     through the exact investigation logic (episode, pattern, calibration).
  3. PRODUCTION arm: labels from all closed cases; model trained on July-October;
     pattern model on every confirmed case; case calibrator from the backtest replay.

Outputs: models/*.joblib, data/store/scored.parquet, benchmark/backtest.json
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.engine import calibration, config, episode, patterns  # noqa: E402
from src.engine.features import build_features, label_table  # noqa: E402
from src.engine.model import train  # noqa: E402
from src.engine.store import build_store, load_closed_cases  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("train")
SPLIT = pd.Timestamp("2016-09-01")
PROD_END = pd.Timestamp("2016-11-01")
rng = np.random.default_rng(7)


def episode_training_rows(df: pd.DataFrame, closed: pd.DataFrame, before=None):
    """True episodes of confirmed closed cases -> (features, pattern)."""
    by_id = df.set_index("TransactionID")
    cust_rows = df.groupby("customer_id").indices
    rows, labels = [], []
    f = closed[closed.outcome == "confirmed_fraud"]
    if before is not None:
        f = f[f.opened_at < before]
    for r in f.itertuples():
        ep = by_id.loc[r.txn_ids.split("|")].reset_index().sort_values("ts")
        cr = df.iloc[cust_rows[r.customer_id]]
        hist = cr[(cr.ts < ep.ts.min()) & (cr.ts >= ep.ts.min() - pd.Timedelta(days=90))]
        rows.append(patterns.episode_features(ep, hist))
        labels.append(r.pattern)
    return rows, labels


def replay(df: pd.DataFrame, closed: pd.DataFrame, pmodel, start, end):
    """Replay closed cases opened in [start, end) through the investigation logic."""
    cust_rows = df.groupby("customer_id").indices
    by_id = df.set_index("TransactionID")
    out = []
    sel = closed[(closed.opened_at >= start) & (closed.opened_at < end)]
    for r in sel.itertuples():
        truth = r.txn_ids.split("|")
        flagged = truth[rng.integers(len(truth))] if r.outcome == "confirmed_fraud" else truth[0]
        if flagged not in by_id.index:
            continue
        fr = by_id.loc[flagged]
        as_of = r.opened_at
        cr = df.iloc[cust_rows[r.customer_id]]
        win = cr[(cr.ts >= fr.ts - pd.Timedelta(days=episode.LOOKBACK_DAYS)) & (cr.ts < as_of)]
        rec = {"case_id": r.case_id, "y": int(r.outcome == "confirmed_fraud"), "pattern": r.pattern,
               "p_flag": float(fr.p_txn), "known_uid": float(fr.known_fraud_uid), "truth": truth,
               "true_exposure": r.exposure_usd}
        for th in (0.2, 0.3, 0.35, 0.4, 0.5, 0.6):
            ep = episode.reconstruct(win, flagged, fr.ts, th)
            s1, s2 = set(ep.ids), set(truth)
            rec[f"jac_{th}"] = len(s1 & s2) / len(s1 | s2)
            rec[f"n_{th}"] = len(ep.ids)
            rec[f"exp_{th}"] = ep.exposure
        ep = episode.reconstruct(win, flagged, fr.ts, episode.MEMBER_THRESHOLD)
        rec["p_max"] = float(ep.txns.p_txn.max())
        rec["n_members"] = int((ep.txns.p_txn >= episode.MEMBER_THRESHOLD).sum())
        if r.outcome == "confirmed_fraud" and pmodel is not None:
            hist = cr[(cr.ts < ep.txns.ts.min()) & (cr.ts >= ep.txns.ts.min() - pd.Timedelta(days=90))]
            feats = patterns.episode_features(ep.txns, hist)
            pp = pmodel.predict_proba(feats)
            rec["pred_pattern"] = max(pp, key=pp.get)
            ct = patterns.detect_card_testing(win)
            st = patterns.detect_structuring(ep.txns)
            if st is not None:
                rec["pred_pattern"] = "undocumented"
            elif ct is not None and len(ep.txns) >= 3:
                rec["pred_pattern"] = "card_testing"
        out.append(rec)
    return pd.DataFrame(out)


def main():
    t0 = time.time()
    build_store()
    base = pd.read_parquet(config.STORE_DIR / "tx.parquet")
    closed = load_closed_cases()
    full_labels = set(label_table(closed).TransactionID)
    report = {"generated": pd.Timestamp.now().isoformat(timespec="seconds")}

    # ---------------- BACKTEST ARM ----------------
    log.info("backtest arm: features with labels opened before %s", SPLIT.date())
    bt = build_features(base, closed, opened_before=SPLIT)
    trm = bt.ts < SPLIT
    m_bt = train(bt, trm)
    bt["p_txn"] = m_bt.score(bt)
    va = (bt.ts >= SPLIT) & (bt.ts < PROD_END)
    yv = bt.loc[va, "TransactionID"].isin(full_labels).astype(int)
    report["transaction_model_sep_oct"] = {
        "n": int(va.sum()), "fraud_rate": round(float(yv.mean()), 4),
        "model_roc_auc": round(float(roc_auc_score(yv, bt.loc[va, "p_txn"])), 4),
        "risk_score_roc_auc": round(float(roc_auc_score(yv, bt.loc[va, "risk_score"])), 4),
        "model_brier": round(float(brier_score_loss(yv, bt.loc[va, "p_txn"])), 5)}
    log.info("txn model sep-oct: %s", report["transaction_model_sep_oct"])
    rows, labels = episode_training_rows(bt, closed, before=SPLIT)
    pm_bt = patterns.train_pattern_model(rows, labels)
    rp = replay(bt, closed, pm_bt, SPLIT, PROD_END)
    # calibrator: fit on September, test on October (then refit on both for production)
    Xall = np.array([calibration.case_vector(a, b, c, d) for a, b, c, d in
                     rp[["p_flag", "p_max", "n_members", "known_uid"]].itertuples(index=False)])
    sep = rp.case_id.map(closed.set_index("case_id").opened_at) < pd.Timestamp("2016-10-01")
    cal_sep = calibration.fit(Xall[sep.values], rp.y.values[sep.values], config.BENCH_PRIOR)
    oct_ = ~sep.values
    p_raw = np.array([cal_sep.raw(x) for x in Xall[oct_]])
    y_oct = rp.y.values[oct_]
    # balanced (50/50) evaluation that mirrors the benchmark mix
    w = np.where(y_oct == 1, 0.5 / y_oct.mean(), 0.5 / (1 - y_oct.mean()))
    p_shift = np.array([cal_sep(x) for x in Xall[oct_]])
    acc_bal = float(np.average((p_shift >= 0.5) == y_oct, weights=w))
    ths = [0.2, 0.3, 0.35, 0.4, 0.5, 0.6]
    fr = rp[rp.y == 1]
    report["case_replay_oct"] = {
        "n_cases": int(oct_.sum()), "fraud_share": round(float(y_oct.mean()), 3),
        "case_roc_auc": round(float(roc_auc_score(y_oct, p_raw)), 4),
        "flagged_txn_score_only_roc_auc": round(float(roc_auc_score(
            y_oct, rp.p_flag.values[oct_])), 4),
        "brier_raw_prior": round(float(brier_score_loss(y_oct, p_raw)), 4),
        "verdict_accuracy_balanced_50_50": round(acc_bal, 4),
        "brier_balanced_50_50": round(float(np.average((p_shift - y_oct) ** 2, weights=w)), 4)}
    report["episode_sep_oct"] = {f"mean_jaccard@{t}": round(float(fr[f"jac_{t}"].mean()), 4) for t in ths}
    report["episode_sep_oct"]["exact_match@chosen"] = round(float((fr[f"jac_{episode.MEMBER_THRESHOLD}"] == 1).mean()), 4)
    report["episode_sep_oct"]["exposure_within_10pct@chosen"] = round(float(
        (abs(fr[f"exp_{episode.MEMBER_THRESHOLD}"] - fr.true_exposure) <= 0.1 * fr.true_exposure).mean()), 4)
    report["pattern_sep_oct"] = {
        "accuracy": round(float((fr.pred_pattern == fr.pattern).mean()), 4),
        "per_pattern": {k: round(float((g.pred_pattern == g.pattern).mean()), 3)
                        for k, g in fr.groupby("pattern")},
        "n": int(len(fr))}
    log.info("replay: %s", json.dumps({k: report[k] for k in ("case_replay_oct", "episode_sep_oct", "pattern_sep_oct")}, indent=1))
    cal = calibration.fit(Xall, rp.y.values, config.BENCH_PRIOR)
    # keep the backtest arm so scripts/backtest.py can replay October end-to-end without leakage
    bt_dir = config.MODELS_DIR / "backtest"
    bt_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(pm_bt, bt_dir / "pattern_model.joblib", compress=3)
    joblib.dump(cal_sep, bt_dir / "case_calibrator.joblib")
    bt[["TransactionID", "p_txn"]].to_parquet(config.STORE_DIR / "scores_backtest.parquet")
    del bt

    # ---------------- PRODUCTION ARM ----------------
    log.info("production arm")
    full = build_features(base, closed)
    m = train(full, full.ts < PROD_END)
    m.meta["backtest_reference"] = report["transaction_model_sep_oct"]
    m.save()
    full["p_txn"] = m.score(full)
    rows, labels = episode_training_rows(full, closed)
    pm = patterns.train_pattern_model(rows, labels)
    joblib.dump(pm, config.MODELS_DIR / "pattern_model.joblib", compress=3)
    joblib.dump(cal, config.MODELS_DIR / "case_calibrator.joblib")
    full.to_parquet(config.STORE_DIR / "scored.parquet")
    report["production"] = {"txn_model_train_rows": m.meta["n_train"],
                            "pattern_model_cases": len(rows),
                            "case_calibrator": {"coef": [round(float(c), 4) for c in cal.lr.coef_[0]],
                                                "features": calibration.CASE_FEATURES,
                                                "train_prior": round(cal.train_prior, 4),
                                                "target_prior": cal.target_prior}}
    config.BENCH_DIR.mkdir(exist_ok=True)
    (config.BENCH_DIR / "backtest.json").write_text(json.dumps(report, indent=2))
    log.info("done in %.0fs", time.time() - t0)


if __name__ == "__main__":
    main()
