#!/usr/bin/env python3
"""End-to-end backtest: replay October closed cases through the full agent.

No leakage: the transaction model, pattern model and case calibrator come from the
backtest arm of train_models.py (trained on July-August, calibrator on September).
Precedent retrieval only sees cases closed before each replayed case opened.
Every replayed alert is presented as a plain risk-score alert (the trigger type is
confounded with the outcome in the history, so we do not give the agent that hint).

Metrics are reported both raw and re-weighted to the benchmark's 50/50 mix.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.engine import config  # noqa: E402
from src.engine.investigator import Investigator  # noqa: E402
from src.engine.memory import CaseMemory  # noqa: E402
from src.engine.runner import load_store  # noqa: E402

logging.basicConfig(level=logging.WARNING)
N_CONFIRMED = int(sys.argv[1]) if len(sys.argv) > 1 else 400
rng = np.random.default_rng(11)


def main():
    t0 = time.time()
    store = load_store()
    bt = pd.read_parquet(config.STORE_DIR / "scores_backtest.parquet").set_index("TransactionID").p_txn
    mem = CaseMemory(store.closed, persist=False)
    agent = Investigator(store, mem, model_dir=config.MODELS_DIR / "backtest", p_by_id=bt)
    cc = store.closed
    octo = cc[(cc.opened_at >= "2016-10-01") & (cc.opened_at < "2016-11-01")]
    cleared = octo[octo.outcome == "cleared"]
    conf = octo[octo.outcome == "confirmed_fraud"]
    conf = conf.iloc[rng.choice(len(conf), size=min(N_CONFIRMED, len(conf)), replace=False)]
    rows = []
    for r in pd.concat([cleared, conf]).sort_values("opened_at").itertuples():
        truth = r.txn_ids.split("|")
        flagged = truth[0] if r.outcome == "cleared" else truth[rng.integers(len(truth))]
        f = store.by_id.loc[flagged]
        case = pd.Series({"case_id": r.case_id, "opened_at": r.opened_at, "trigger_type": "risk_score",
                          "trigger_text": f"Real-time model scored transaction {flagged} at {f.risk_score:.2f}.",
                          "flagged_txn_id": flagged, "card_id": f.card_id, "customer_id": f.customer_id,
                          "risk_score": f.risk_score})
        mem.agent_cases = []            # each replay is independent
        a, _ = agent.investigate(case)
        c = a["case"]
        fin = {x["action"] for x in a["next_best_actions"]["final"]}
        aff = set(c["affected_txn_ids"])
        y = int(r.outcome == "confirmed_fraud")
        rows.append({
            "case_id": r.case_id, "y": y, "pattern": r.pattern, "verdict": c["verdict"],
            "p": c["fraud_probability"], "pred_pattern": c["pattern"],
            "jaccard": len(aff & set(truth)) / len(aff | set(truth)) if aff else 0.0,
            "exposure": c["exposure_usd"], "true_exposure": r.exposure_usd,
            "blocked": int("BLOCK_CARD" in fin), "closed_no_fraud": int("CLOSE_NO_FRAUD" in fin),
            "sar": int(a["sar"]["file"]), "true_sar": int(r.report_filed == "Yes"),
            "asked": int(bool(a["evidence_requests"])), "tool_calls": a["tool_calls"], "latency": a["latency_s"]})
    d = pd.DataFrame(rows)
    w = np.where(d.y == 1, 0.5 / d.y.mean(), 0.5 / (1 - d.y.mean()))
    dec = d.verdict != "uncertain"
    correct = ((d.verdict == "fraud") & (d.y == 1)) | ((d.verdict == "legitimate") & (d.y == 0))
    fr = d[d.y == 1]
    frd = fr[fr.verdict != "legitimate"]
    le = d[d.y == 0]
    from sklearn.metrics import roc_auc_score
    rep = {
        "n_cases": len(d), "n_confirmed": int(d.y.sum()), "n_cleared": int((1 - d.y).sum()),
        "setup": "October 2016 closed cases; models trained on Jul-Aug (calibrator on Sep); all alerts presented as risk_score triggers",
        "case_probability_roc_auc": round(float(roc_auc_score(d.y, d.p)), 4),
        "brier_balanced": round(float(np.average((d.p - d.y) ** 2, weights=w)), 4),
        "verdict_accuracy_balanced_incl_uncertain_as_wrong": round(float(np.average(correct, weights=w)), 4),
        "verdict_accuracy_balanced_on_decided": round(float(np.average(correct[dec], weights=w[dec])), 4),
        "uncertain_rate": round(float(1 - dec.mean()), 4),
        "fraud_recall": round(float((fr.verdict == "fraud").mean()), 4),
        "legit_recall": round(float((le.verdict == "legitimate").mean()), 4),
        "episode_mean_jaccard_on_fraud_found": round(float(frd.jaccard.mean()), 4),
        "episode_exact_match_on_fraud_found": round(float((frd.jaccard == 1).mean()), 4),
        "exposure_within_10pct_on_fraud_found": round(float(((frd.exposure - frd.true_exposure).abs() <= 0.1 * frd.true_exposure).mean()), 4),
        "pattern_accuracy_on_fraud_verdicts": round(float((fr[fr.verdict == "fraud"].pred_pattern == fr[fr.verdict == "fraud"].pattern).mean()), 4),
        "block_rate_on_cleared": round(float(le.blocked.mean()), 4),
        "block_rate_on_confirmed": round(float(fr.blocked.mean()), 4),
        "sar_agreement_on_confirmed": round(float((fr.sar == fr.true_sar).mean()), 4),
        "evidence_request_rate": round(float(d.asked.mean()), 4),
        "mean_tool_calls": round(float(d.tool_calls.mean()), 1),
        "mean_latency_s": round(float(d.latency.mean()), 3),
        "runtime_s": round(time.time() - t0, 1),
    }
    out = config.BENCH_DIR / "case_backtest.json"
    out.write_text(json.dumps(rep, indent=2))
    d.to_csv(config.BENCH_DIR / "case_backtest_rows.csv", index=False)
    print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
