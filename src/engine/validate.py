"""Answer-file contract validator.

Checks every requirement of the dataset README's answer format and Fraud Policy v1.0
that can be checked mechanically. ``python scripts/validate_answers.py`` exits non-zero
on any violation; CI (tests/test_answers.py) runs it over cases/.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .policy import route

ACTIONS = {"ALLOW_TRANSACTION", "DECLINE_TRANSACTION", "MONITOR_CARD", "MONITOR_CONNECTED_CARDS", "WARN_CUSTOMER",
           "VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH", "BLOCK_CARD", "BLOCK_ALL_CARDS", "GENERATE_REPORT",
           "CREATE_CASE", "FILE_REPORT", "ESCALATE_TO_ANALYST", "CLOSE_NO_FRAUD"}
PATTERNS = {"card_testing", "card_not_present_fraud", "card_not_present_new_device", "out_of_region_use",
            "account_takeover", "undocumented", "none"}
STATUS = {"open", "closed_fraud", "closed_legitimate", "escalated"}
VERDICT = {"fraud", "legitimate", "uncertain"}
SOURCES = {"graph", "document", "customer", "external"}
REQ_TYPES = {"customer_validation", "step_up_auth", "analyst_info"}
RULE = re.compile(r"\bR(10|[1-9])\b|3a|3b|\b6\b|Policy")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate(ans: dict, store=None, case_row=None, closed_ids=None) -> list[str]:
    """Check one answer file.

    Tier A (always): the answer-format contract, Fraud Policy v1.0 routing/invariants, id shapes and
    closed-case references. ``case_pack.csv`` and ``closed_cases_history.csv`` are checked in, so tier A
    needs no install beyond pandas.
    Tier B (needs ``store``, i.e. ``main.py build``): ids exist in the 590k-transaction graph, exposure
    equals the sum of the cited transactions, and the first suspicious txn is the earliest one.
    """
    v = []
    e = v.append
    for k in ("case_id", "case", "evidence_requests", "next_best_actions", "sar", "stop_reason", "tool_calls",
              "tokens", "latency_s"):
        if k not in ans:
            e(f"missing top-level {k}")
    c = ans.get("case", {})
    for k in ("status", "verdict", "fraud_probability", "pattern", "pattern_description", "affected_txn_ids",
              "first_suspicious_txn_id", "connected_card_ids", "connected_device_profiles", "exposure_usd",
              "evidence", "similar_prior_cases", "summary", "written_to_graph", "graph_case_id"):
        if k not in c:
            e(f"missing case.{k}")
    if v:
        return v
    if c["status"] not in STATUS:
        e("bad status")
    if c["verdict"] not in VERDICT:
        e("bad verdict")
    if c["pattern"] not in PATTERNS:
        e("bad pattern")
    p = c["fraud_probability"]
    if not (0 <= p <= 1):
        e("probability out of range")
    if c["pattern"] == "undocumented" and len(c["pattern_description"].split(".")) < 3:
        e("undocumented pattern needs a 2-3 sentence description")
    if c["pattern"] != "undocumented" and c["pattern_description"]:
        e("pattern_description must be empty unless undocumented")
    aff = c["affected_txn_ids"]
    if c["verdict"] == "legitimate":
        if aff or c["exposure_usd"] != 0 or ans["sar"]["file"]:
            e("legitimate verdict needs empty affected, exposure 0, no SAR")
        if c["pattern"] != "none":
            e("legitimate verdict should have pattern none")
    else:
        if case_row is not None and case_row.flagged_txn_id not in aff:
            e("flagged txn missing from affected_txn_ids")
        if aff and c["first_suspicious_txn_id"] not in aff:
            e("first_suspicious not in affected")
    esc = any(a["action"] == "ESCALATE_TO_ANALYST" for a in ans["next_best_actions"]["final"])
    if esc and c["status"] != "escalated":
        e("final escalates but status is not escalated")
    if c["verdict"] == "fraud" and not esc and c["status"] != "closed_fraud":
        e("fraud verdict should be closed_fraud")
    if c["verdict"] == "legitimate" and c["status"] != "closed_legitimate":
        e("legitimate verdict should be closed_legitimate")
    if c["verdict"] == "uncertain" and c["status"] not in ("open", "escalated"):
        e("uncertain verdict should be open/escalated")
    if c["verdict"] == "fraud" and p < 0.5:
        e("fraud verdict with p<0.5")
    if c["verdict"] == "legitimate" and p > 0.5:
        e("legitimate verdict with p>0.5")
    if c["written_to_graph"] and not c["graph_case_id"]:
        e("written_to_graph without graph_case_id")
    if not c["written_to_graph"] and c["graph_case_id"]:
        e("graph_case_id without a graph write")
    if len(c["summary"].split(". ")) > 7:
        e("summary longer than six sentences")
    for ev in c["evidence"]:
        if set(ev) != {"claim", "source", "ref", "entity_ids"} or ev["source"] not in SOURCES:
            e(f"bad evidence item {ev}")
    # id shapes and closed-case references: dataset-grounded but need no parquet store
    for t in aff + ([c["first_suspicious_txn_id"]] if c["first_suspicious_txn_id"] else []):
        if not re.fullmatch(r"\d{1,12}", str(t)):
            e(f"txn id is not a numeric string: {t!r}")
    for cd in c["connected_card_ids"]:
        if not re.fullmatch(r"C\d{5}-K\d", str(cd)):
            e(f"connected card id does not match C#####-K#: {cd!r}")
    for dp in c["connected_device_profiles"]:
        if "|" not in dp:
            e(f"device profile is not a labelled profile: {dp!r}")
    if len(set(c["similar_prior_cases"])) != len(c["similar_prior_cases"]):
        e("duplicate id in similar_prior_cases")
    if closed_ids is not None:
        for cc in c["similar_prior_cases"]:
            if cc not in closed_ids:
                e(f"cited closed case {cc} is not in closed_cases_history.csv")
    if case_row is not None and case_row.card_id in c["connected_card_ids"]:
        e("connected_card_ids should list OTHER cards")
    # ids exist
    if store is not None:
        ids = store.id_set
        for t in aff:
            if t not in ids:
                e(f"unknown txn {t}")
        if aff and case_row is not None:
            rows = store.by_id.loc[aff]
            exp = round(float(rows.TransactionAmt.abs().sum()), 2)
            if abs(exp - c["exposure_usd"]) > 0.011:
                e(f"exposure {c['exposure_usd']} != sum {exp}")
            if rows.TransactionID.iloc[rows.ts.argmin()] != c["first_suspicious_txn_id"]:
                e("first_suspicious_txn_id is not the earliest affected txn")
        for cd in c["connected_card_ids"]:
            if cd not in store.card_set:
                e(f"unknown card {cd}")
        for cc in c["similar_prior_cases"]:
            if cc not in store.closed_case_set:
                e(f"unknown closed case {cc}")
        known = store.id_set | store.card_set | store.customer_set | store.closed_case_set
        for ev in c["evidence"]:
            for x in ev["entity_ids"]:
                if x not in known and not str(x).startswith("HHG-"):
                    e(f"unknown entity id {x}")
    # evidence requests
    for r in ans["evidence_requests"]:
        if r.get("type") not in REQ_TYPES or not isinstance(r.get("asked_after_step"), int) or not r.get("assumed_response"):
            e(f"bad evidence request {r}")
    # actions
    nba = ans["next_best_actions"]
    for stage in ("initial", "final"):
        if not nba[stage]:
            e(f"empty {stage} actions")
        seen = set()
        for a in nba[stage]:
            if a["action"] not in ACTIONS:
                e(f"unknown action {a['action']}")
                continue
            if a["action"] in seen:
                e(f"duplicate {a['action']}")
            seen.add(a["action"])
            exp_route = route(a["action"], c["exposure_usd"])
            if a["route"] != exp_route:
                e(f"{stage} {a['action']} route {a['route']} != {exp_route}")
            if not RULE.search(a.get("reason", "")):
                e(f"{stage} {a['action']} reason cites no rule")
    if not ans["evidence_requests"] and nba["initial"] != nba["final"]:
        e("no evidence requested but final != initial")
    if not ans["evidence_requests"] and nba["what_changed"] != "nothing":
        e("what_changed must be 'nothing' when nothing requested")
    fin = {a["action"] for a in nba["final"]}
    ini = {a["action"] for a in nba["initial"]}
    sar = ans["sar"]
    if sar["file"] != ("FILE_REPORT" in fin):
        e("sar.file disagrees with FILE_REPORT in final")
    if sar["file"]:
        n = len([s for s in re.split(r"(?<=[.!?])\s+", sar["narrative"]) if s.strip()])
        if not 6 <= n <= 12:
            e(f"SAR narrative has {n} sentences (6-12 required)")
        if abs(sar["total_amount_usd"] - c["exposure_usd"]) > 0.011:
            e("SAR amount != exposure")
        if len(sar["activity_dates"]) != 2 or not all(DATE.match(d) for d in sar["activity_dates"]):
            e("bad activity_dates")
        if not sar["subjects"]:
            e("SAR without subjects")
        if not RULE.search(sar["reason"]):
            e("SAR reason cites no rule")
        if "CREATE_CASE" not in fin:
            e("a report needs a case")
    else:
        if sar["narrative"] or sar["subjects"] or sar["total_amount_usd"] or sar["activity_dates"]:
            e("sar fields must be empty when file is false")
    # policy invariants
    for stage, acts in (("initial", ini), ("final", fin)):
        if "BLOCK_ALL_CARDS" in acts:
            e(f"{stage}: BLOCK_ALL_CARDS needs R10 conditions (never met here)")
    if c["verdict"] == "legitimate" and fin & {"BLOCK_CARD", "DECLINE_TRANSACTION", "FILE_REPORT", "MONITOR_CONNECTED_CARDS"}:
        e("legitimate verdict with adverse actions")
    if "MONITOR_CONNECTED_CARDS" in fin and not c["connected_card_ids"]:
        e("MONITOR_CONNECTED_CARDS without connected cards")
    if c["verdict"] == "uncertain" and c["exposure_usd"] > 500 and "ESCALATE_TO_ANALYST" not in fin:
        e("R8: uncertain and exposure > 500 without escalation")
    if ans["evidence_requests"] and not (ini & {"VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH"}):
        e("evidence requested but initial has no VERIFY/STEP_UP")
    if ans["evidence_requests"] and "CREATE_CASE" not in (ini | fin):
        e("3a: evidence requested without a case")
    if p >= 0.30 and "CREATE_CASE" not in (ini | fin) and c["verdict"] != "legitimate":
        e("3a: p>=0.30 without CREATE_CASE")
    if "BLOCK_CARD" in ini and p < 0.70 and not ans["evidence_requests"]:
        e("R1: block below 0.70 without verification")
    return v


def validate_dir(d: Path, store=None, case_pack=None, closed_ids=None) -> dict[str, list[str]]:
    rows = {r.case_id: r for r in case_pack.itertuples()} if case_pack is not None else {}
    out = {}
    for f in sorted(Path(d).glob("HHG-*.json")):
        a = json.loads(f.read_text())
        out[f.stem] = validate(a, store, rows.get(f.stem), closed_ids)
    return out


N_CHECKS = sum(1 for _ in re.finditer(r"\be\(", Path(__file__).read_text()))
