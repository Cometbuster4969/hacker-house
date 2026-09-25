#!/usr/bin/env python3
"""Autonomous monitoring beyond the 20 case-pack cases.

Sweeps November-December (the period with no closed cases) for the patterns the agent
learned to recognise, opens its own alerts, and investigates each with the same agent
and policy engine. Nothing here reads the case pack.

  1. Device rings    — device profiles used by >= 3 customers in 30 days, >= 80% of rows
                       marked New and behind a proxy (graph: ring_candidates).
  2. Structuring     — cards with >= 3 online purchases just under $500 inside an hour.
  3. Card testing    — >= 3 sub-$5 online authorizations inside an hour, then a larger
                       purchase within 24 h (policy R5) that the model also scores >= 0.05.

Output: monitoring/alerts.json and monitoring/cases/MON-*.json (same answer schema).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.engine import config, patterns  # noqa: E402
from src.engine.investigator import Investigator  # noqa: E402
from src.engine.memory import CaseMemory  # noqa: E402
from src.engine.runner import load_store  # noqa: E402
from src.engine.store import device_label  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("monitor")
OUT = config.ROOT / "monitoring"
START, END = pd.Timestamp("2016-11-01"), pd.Timestamp("2017-01-01")
MAX_CT = 10
# Measured on Jul-Oct: the R5 sequence alone precedes a confirmed-fraud purchase in 2 of 11
# detections (18% vs 3.4% base rate) - a lead, not proof. Only open an alert when the model
# independently finds the follow-up purchase unusual for the cardholder.
MIN_P_CT = 0.05


def main():
    store = load_store()
    df = store.df
    w = df[(df.ts >= START) & (df.ts < END)]
    alerts = []
    # 1. rings --------------------------------------------------------------
    dv = w[w.device_profile_id.notna()]
    g = dv.groupby("device_profile_id").agg(n_cust=("customer_id", "nunique"), n=("TransactionID", "size"),
                                            new_proxy=("id_15", lambda s: 0))
    np_share = dv.assign(np=(dv.id_15.eq("New") & dv.id_23.notna())).groupby("device_profile_id").np.mean()
    cand = g[(g.n_cust >= 3)].join(np_share)
    cand = cand[cand.np >= 0.8].sort_values("n_cust", ascending=False)
    for dev, r in cand.iterrows():
        rows = dv[dv.device_profile_id == dev].sort_values("ts")
        alerts.append({"type": "device_ring", "element": device_label(dev), "n_customers": int(r.n_cust),
                       "n_txns": int(r.n), "first": str(rows.ts.min()), "last": str(rows.ts.max()),
                       "cards": sorted(set(rows.card_id)), "seed_txn": rows.TransactionID.iloc[-1],
                       "as_of": rows.ts.iloc[-1] + pd.Timedelta(hours=1)})
    # 2. structuring --------------------------------------------------------
    s = w[(w.channel == "online") & (w.TransactionAmt < 500) & (w.TransactionAmt >= 425)]
    for card, gg in s.groupby("card_id"):
        if len(gg) < 3:
            continue
        hit = patterns.detect_structuring(gg)
        if hit:
            rows = hit["rows"]
            alerts.append({"type": "structuring", "element": card, "n_txns": hit["n"], "total": hit["total"],
                           "first": str(rows.ts.min()), "last": str(rows.ts.max()), "cards": [card],
                           "seed_txn": rows.TransactionID.iloc[-1], "as_of": rows.ts.max() + pd.Timedelta(hours=1)})
    # 3. card testing ------------------------------------------------------
    sm = w[(w.TransactionAmt < 5) & (w.channel == "online")]
    ct = []
    for card, gg in sm.groupby("card_id"):
        if len(gg) < 3:
            continue
        hit = patterns.detect_card_testing(w[w.card_id == card])
        if hit:
            p = store.by_id.loc[hit["purchase"]].p_txn
            if p >= MIN_P_CT:
                ct.append((p, card, hit))
    ct.sort(key=lambda x: -x[0])
    for p, card, hit in ct[:MAX_CT]:
        rows = hit["rows"]
        alerts.append({"type": "card_testing", "element": card, "n_txns": len(rows), "model_p_purchase": round(float(p), 3),
                       "first": str(rows.ts.min()), "last": str(rows.ts.max()), "cards": [card],
                       "seed_txn": hit["purchase"], "as_of": rows.ts.max() + pd.Timedelta(hours=1)})
    log.info("%d alerts (%d rings, %d structuring, %d card testing of %d found)", len(alerts),
             sum(a["type"] == "device_ring" for a in alerts), sum(a["type"] == "structuring" for a in alerts),
             min(len(ct), MAX_CT), len(ct))

    # investigate each alert with the same agent ---------------------------------
    mem = CaseMemory(store.closed, persist=False)
    agent = Investigator(store, mem)
    (OUT / "cases").mkdir(parents=True, exist_ok=True)
    for old in (OUT / "cases").glob("MON-*.json"):
        old.unlink()
    alerts.sort(key=lambda a: a["as_of"])
    for i, a in enumerate(alerts, 1):
        f = store.by_id.loc[a["seed_txn"]]
        cid = f"MON-{i:03d}"
        text = {"device_ring": f"Autonomous monitor: device profile {a['element']} used by {a.get('n_customers')} customers "
                               f"with New-device + proxy on most rows. Review transaction {f.TransactionID}.",
                "structuring": f"Autonomous monitor: sub-$500 online burst on {a['element']}. Review transaction {f.TransactionID}.",
                "card_testing": f"Autonomous monitor: small-authorization sequence then a purchase on {a['element']}. "
                                f"Review transaction {f.TransactionID}."}[a["type"]]
        case = pd.Series({"case_id": cid, "opened_at": a["as_of"], "trigger_type": "analyst_request" if a["type"] == "device_ring"
                          else "risk_score", "trigger_text": text, "flagged_txn_id": f.TransactionID,
                          "card_id": f.card_id, "customer_id": f.customer_id, "risk_score": f.risk_score})
        ans, trace = agent.investigate(case)
        ans["monitoring_alert"] = {k: (str(v) if k == "as_of" else v) for k, v in a.items()}
        (OUT / "cases" / f"{cid}.json").write_text(json.dumps(ans, indent=2, default=str))
        a["case_file"] = f"cases/{cid}.json"
        a["verdict"] = ans["case"]["verdict"]
        a["p"] = ans["case"]["fraud_probability"]
        a["final_actions"] = [x["action"] for x in ans["next_best_actions"]["final"]]
        a["as_of"] = str(a["as_of"])
        log.info("%s %-12s %-40s -> %s p=%.2f %s", cid, a["type"], a["element"][:40], a["verdict"], a["p"],
                 ",".join(a["final_actions"]))
    (OUT / "alerts.json").write_text(json.dumps({"window": [str(START.date()), str(END.date())],
                                                 "n_alerts": len(alerts), "alerts": alerts}, indent=2, default=str))


if __name__ == "__main__":
    main()
