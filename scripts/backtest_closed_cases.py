#!/usr/bin/env python3
"""Backtest the rule engine against closed cases with KNOWN outcomes.

RULES-ONLY by default: zero LLM calls, zero API cost, deterministic.
For every closed case in closed_cases_history.csv it replays the same
pipeline steps the agent uses (evidence gathering -> pattern detection ->
calculate_fraud_probability) with the case's flagged transaction as the
trigger, then measures how well the score discriminates
confirmed_fraud (label 1) from cleared (label 0), and derives empirical
per-pattern anchors P(confirmed_fraud | pattern).

Optional:
  --llm-sample K   also run the FULL hybrid (LLM judge) on a balanced sample
                   of K cases (K/2 per outcome). Costs ~K API calls; uses the
                   configured provider + cache. Default: off.

Usage:
  python scripts/backtest_closed_cases.py                    # rules-only, all cases
  python scripts/backtest_closed_cases.py --limit 500        # quick pass
  python scripts/backtest_closed_cases.py --llm-sample 50    # + hybrid on 50

Output: printed report + cases/backtest_results.json
"""
import argparse
import csv
import json
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.graph.in_memory_graph import InMemoryGraph
from src.graph.data_loader import DataLoader
from src.evidence.gatherer import EvidenceGatherer
from src.evidence.pattern_detector import PatternDetector
from src.utils.models import CasePackEntry, InvestigationState

OUT_PATH = ROOT / "cases" / "backtest_results.json"


# ─────────────────────────── metrics (stdlib only) ───────────────────────────

def auc_mw(pairs):
    """AUC via Mann-Whitney U. pairs = [(score, label 0/1)]. Handles ties."""
    labeled = sorted(pairs, key=lambda p: p[0])
    n1 = sum(1 for _, y in labeled if y == 1)
    n0 = len(labeled) - n1
    if n1 == 0 or n0 == 0:
        return None
    # average ranks (1-based) with ties sharing the mean rank
    ranks, i = [0.0] * len(labeled), 0
    while i < len(labeled):
        j = i
        while j + 1 < len(labeled) and labeled[j + 1][0] == labeled[i][0]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    rank_sum = sum(r for r, (_, y) in zip(ranks, labeled) if y == 1)
    return (rank_sum - n1 * (n1 + 1) / 2) / (n1 * n0)


def confusion(pairs, thr):
    tp = sum(1 for s, y in pairs if s >= thr and y == 1)
    fp = sum(1 for s, y in pairs if s >= thr and y == 0)
    fn = sum(1 for s, y in pairs if s < thr and y == 1)
    tn = sum(1 for s, y in pairs if s < thr and y == 0)
    acc = (tp + tn) / len(pairs) if pairs else 0.0
    return {"threshold": thr, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "accuracy": round(acc, 4)}


def best_threshold(pairs):
    best, best_acc = 0.5, -1.0
    for thr in [i / 100 for i in range(5, 100, 5)]:
        acc = confusion(pairs, thr)["accuracy"]
        if acc > best_acc:
            best, best_acc = thr, acc
    return best, best_acc


def pattern_table(rows):
    """Empirical anchors: per pattern value, P(confirmed_fraud | pattern)."""
    groups = defaultdict(lambda: {"confirmed_fraud": 0, "cleared": 0, "probs": []})
    for r in rows:
        g = groups[r["pattern"]]
        g[r["outcome"]] = g.get(r["outcome"], 0) + 1
        g["probs"].append(r["score"])
    table = {}
    for pat, g in sorted(groups.items()):
        n = g["confirmed_fraud"] + g["cleared"]
        if not n:
            continue
        table[pat] = {
            "n": n,
            "p_confirmed_fraud": round(g["confirmed_fraud"] / n, 4),
            "mean_score": round(statistics.mean(g["probs"]), 4) if g["probs"] else None,
            "proposed_anchor": round((g["confirmed_fraud"] + 1) / (n + 2), 4),  # Laplace
        }
    return table


# ─────────────────────────── performance ───────────────────────────

def install_neighbor_index(graph) -> None:
    """One-time index over FROM_DEVICE edges; monkey-patches get_neighbors.

    The stock get_neighbors linearly scans the full edge list per call, and
    detect_new_device calls it once per card transaction (~40 x 144K scans per
    case). This preserves the exact return format while making lookups O(1).
    """
    print("building FROM_DEVICE neighbor index (one pass)...")
    index = {}
    for src_id, tgt_id, props in graph.edges.get(
            ("Transaction", "FROM_DEVICE", "DeviceProfile"), []):
        tgt = graph.get_vertex("DeviceProfile", tgt_id)
        if tgt:
            index.setdefault(src_id, []).append({**tgt, **props, "_edge": "FROM_DEVICE"})
    original = graph.get_neighbors

    def fast_get_neighbors(vtype, vid, edge_name, tgt_type, direction="out"):
        if (direction == "out" and vtype == "Transaction"
                and edge_name == "FROM_DEVICE" and tgt_type == "DeviceProfile"):
            return index.get(vid, [])
        return original(vtype, vid, edge_name, tgt_type, direction)

    graph.get_neighbors = fast_get_neighbors
    print(f"  indexed {sum(len(v) for v in index.values())} FROM_DEVICE edges "
          f"over {len(index)} transactions")


# ─────────────────────────── backtest core ───────────────────────────

def build_trigger(row):
    txn = row.get("first_fraud_txn_id") or ""
    if not txn and row.get("txn_ids"):
        txn = row["txn_ids"].split("|")[0]
    if not txn:
        return None
    return CasePackEntry(
        case_id=row["case_id"],
        customer_id=row["customer_id"],
        card_id=row["card_id"],
        opened_at=row.get("opened_at", "") or "",
        trigger_type="analyst_request",
        trigger_text=f"Backtest replay of closed case {row['case_id']} "
                     f"(historical outcome: {row['outcome']})",
        flagged_txn_id=txn,
    )


def score_case(gatherer, detector, trigger):
    """Replay pipeline steps 1-3 + detection + rule score. No LLM.

    Data-window note: closed-case transaction IDs (T00xxxxx) fall outside the
    loaded transactions.csv window (IDs 3.0M-3.58M), so the specific flagged
    txn is usually absent. The gatherers degrade gracefully; scoring then
    rests on the card's transaction history, velocity, and customer context.
    Cases whose card has NO history in the window carry no signal -> skipped.
    """
    state = InvestigationState(case_id=trigger.case_id, trigger=trigger)
    state = gatherer.gather_initial_evidence(state)
    state = gatherer.gather_device_evidence(state)
    state = gatherer.gather_velocity_evidence(state)
    state = detector.detect_patterns(state)
    if not state.card_transactions:
        return None  # card absent from loaded window -> no evidence signal
    score = detector.calculate_fraud_probability(state)
    return {"score": round(float(score), 4),
            "pattern": state.pattern.value,
            "txn_in_window": state.flagged_txn is not None,
            "n_card_txns": len(state.card_transactions),
            "n_evidence": len(state.evidence_collected)}


def run_rules_backtest(rows, gatherer, detector, limit):
    pairs, details, skipped = [], [], defaultdict(int)
    t0 = time.time()
    for i, row in enumerate(rows[:limit] if limit else rows):
        label = 1 if row["outcome"] == "confirmed_fraud" else \
            0 if row["outcome"] == "cleared" else None
        if label is None:
            skipped["unknown_outcome"] += 1
            continue
        trigger = build_trigger(row)
        if trigger is None:
            skipped["no_flagged_txn"] += 1
            continue
        try:
            result = score_case(gatherer, detector, trigger)
        except Exception as e:
            skipped[f"error:{type(e).__name__}"] += 1
            continue
        if result is None:
            skipped["txn_not_in_graph"] += 1
            continue
        pairs.append((result["score"], label))
        details.append({"case_id": row["case_id"], "outcome": row["outcome"],
                        "labeled_pattern": row.get("pattern", ""),
                        **result})
        if (i + 1) % 200 == 0:
            rate = (i + 1) / (time.time() - t0)
            total = len(rows[:limit] if limit else rows)
            print(f"  scored {i + 1} ({time.time() - t0:.0f}s, {rate:.1f}/s, "
                  f"ETA {(total - i - 1) / rate:.0f}s)")
    return pairs, details, skipped


def report(pairs, details, table_detected, table_labeled, skipped, elapsed):
    pos = [s for s, y in pairs if y == 1]
    neg = [s for s, y in pairs if y == 0]
    auc = auc_mw(pairs)
    thr, acc = best_threshold(pairs)

    lines = []
    lines.append("=" * 68)
    lines.append("BACKTEST: rule engine vs closed cases with known outcomes")
    lines.append("=" * 68)
    lines.append(f"cases scored      : {len(pairs)} "
                 f"(confirmed_fraud={len(pos)}, cleared={len(neg)})")
    if skipped:
        lines.append(f"skipped           : {dict(skipped)}")
    lines.append(f"elapsed           : {elapsed:.0f}s")
    if auc is not None:
        lines.append(f"\nAUC (discrimination): {auc:.4f}")
        lines.append(f"  0.5 = coin flip, 1.0 = perfect ranking")
        lines.append(f"\nscore separation:")
        lines.append(f"  confirmed_fraud : mean {statistics.mean(pos):.3f} "
                     f"std {statistics.stdev(pos):.3f}" if len(pos) > 1 else "")
        lines.append(f"  cleared         : mean {statistics.mean(neg):.3f} "
                     f"std {statistics.stdev(neg):.3f}" if len(neg) > 1 else "")
        c05 = confusion(pairs, 0.5)
        lines.append(f"\nconfusion @ 0.50  : acc={c05['accuracy']:.3f} "
                     f"(tp={c05['tp']} fp={c05['fp']} fn={c05['fn']} tn={c05['tn']})")
        lines.append(f"best threshold    : {thr:.2f} -> accuracy {acc:.3f}")
    lines.append("\nEmpirical anchors — DETECTED pattern (rule engine's view):")
    for pat, t in table_detected.items():
        lines.append(f"  {pat:30} n={t['n']:4}  P(fraud)={t['p_confirmed_fraud']:.3f}  "
                     f"mean_score={t['mean_score']}  anchor*={t['proposed_anchor']}")
    lines.append("\nEmpirical anchors — LABELED pattern (dataset's view):")
    for pat, t in table_labeled.items():
        lines.append(f"  {pat:30} n={t['n']:4}  P(fraud)={t['p_confirmed_fraud']:.3f}  "
                     f"anchor*={t['proposed_anchor']}")
    lines.append("\n* anchor = Laplace-smoothed P(confirmed_fraud | pattern) — the")
    lines.append("  empirically-derived replacement for the hand-set weights in")
    lines.append("  PatternDetector.calculate_fraud_probability.")
    lines.append("Caveat: closed cases are a SELECTED sample (already flagged and")
    lines.append("investigated), so base rates are inflated vs the wild. Valid for")
    lines.append("discrimination and per-pattern calibration, not absolute rates.")
    return "\n".join(lines)


# ─────────────────────────── optional LLM slice ───────────────────────────

def run_llm_sample(rows, pairs_by_id, k, gatherer, detector):
    """Full hybrid (LLM judge) on a balanced sample of k cases."""
    from src.agent.llm_reasoner import LLMReasoner
    from src.evidence.graphrag import GraphRAGSynthesizer

    llm = LLMReasoner()
    if not llm.is_llm_available:
        print("\nLLM not available — skipping --llm-sample.")
        return None
    syn = GraphRAGSynthesizer(gatherer.graph)

    fraud_ids = [r["case_id"] for r in rows if r["outcome"] == "confirmed_fraud"]
    clear_ids = [r["case_id"] for r in rows if r["outcome"] == "cleared"]
    rng = random.Random(42)
    sample_ids = set(rng.sample(fraud_ids, min(k // 2, len(fraud_ids))) +
                     rng.sample(clear_ids, min(k - k // 2, len(clear_ids))))

    results, y_true, y_rules, y_llm = [], [], [], []
    print(f"\nLLM sample: {len(sample_ids)} cases (~{len(sample_ids)} API calls)...")
    for row in rows:
        if row["case_id"] not in sample_ids:
            continue
        trigger = build_trigger(row)
        scored = score_case(gatherer, detector, trigger)
        if scored is None:
            continue
        state = InvestigationState(case_id=row["case_id"], trigger=trigger)
        state = gatherer.gather_initial_evidence(state)
        state = gatherer.gather_device_evidence(state)
        state = gatherer.gather_velocity_evidence(state)
        state = detector.detect_patterns(state)
        ctx = syn.build_investigation_context(state)
        anchor = scored["score"]
        try:
            assessment = llm.assess_evidence(state, ctx, anchor)
        except Exception as e:
            print(f"  {row['case_id']}: LLM error {type(e).__name__}")
            continue
        verdict = (assessment.get("verdict") or "uncertain").lower()
        llm01 = 1 if verdict == "fraud" else 0 if verdict == "legitimate" else 0.5
        rules01 = 1 if anchor >= 0.5 else 0
        y_true.append(1 if row["outcome"] == "confirmed_fraud" else 0)
        y_rules.append(rules01)
        y_llm.append(llm01)
        results.append({"case_id": row["case_id"], "outcome": row["outcome"],
                        "rule_score": anchor, "llm_verdict": verdict,
                        "llm_prob": assessment.get("fraud_probability")})

    def acc(preds):
        hard = [1 if p >= 0.5 else 0 for p in preds]
        return round(sum(1 for p, t in zip(hard, y_true) if p == t) / len(y_true), 4)

    if not y_true:
        return None
    out = {"n": len(y_true),
           "rules_accuracy": acc(y_rules),
           "hybrid_accuracy": acc(y_llm),
           "results": results}
    print(f"  rules-only accuracy : {out['rules_accuracy']}")
    print(f"  hybrid accuracy     : {out['hybrid_accuracy']}")
    return out


# ─────────────────────────── main ───────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT / "data" / "HHGOA_IEEE"))
    ap.add_argument("--max-transactions", type=int, default=0, help="0 = full load")
    ap.add_argument("--limit", type=int, default=0, help="cap closed cases scored")
    ap.add_argument("--llm-sample", type=int, default=0, help="hybrid sample size (API cost)")
    args = ap.parse_args()

    csv_path = Path(args.data_dir) / "closed_cases_history.csv"
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    rows = [r for r in rows if r.get("outcome") in ("confirmed_fraud", "cleared")]
    print(f"closed cases with known outcomes: {len(rows)}")

    print("loading graph (this takes ~2 min on the full dataset)...")
    graph = InMemoryGraph()
    DataLoader(args.data_dir, graph).load_all(max_transactions=args.max_transactions)

    gatherer = EvidenceGatherer(graph)
    detector = PatternDetector()
    install_neighbor_index(graph)

    t0 = time.time()
    pairs, details, skipped = run_rules_backtest(rows, gatherer, detector, args.limit)
    elapsed = time.time() - t0
    if not pairs:
        print("No cases could be scored — check skip reasons above.")
        sys.exit(1)

    table_detected = pattern_table(
        [{"pattern": d["pattern"], "outcome": d["outcome"], "score": d["score"]}
         for d in details])
    table_labeled = pattern_table(
        [{"pattern": d["labeled_pattern"] or "none", "outcome": d["outcome"],
          "score": d["score"]} for d in details])

    print(report(pairs, details, table_detected, table_labeled, skipped, elapsed))

    result = {
        "n_scored": len(pairs),
        "n_confirmed_fraud": sum(1 for _, y in pairs if y == 1),
        "n_cleared": sum(1 for _, y in pairs if y == 0),
        "auc": auc_mw(pairs),
        "best_threshold": best_threshold(pairs)[0],
        "best_accuracy": best_threshold(pairs)[1],
        "anchors_detected_pattern": table_detected,
        "anchors_labeled_pattern": table_labeled,
        "skipped": dict(skipped),
        "elapsed_s": round(elapsed, 1),
        "note": "rules-only replay; selected-sample caveat applies",
    }
    if args.llm_sample:
        llm_out = run_llm_sample(rows, {d["case_id"]: d for d in details},
                                 args.llm_sample, gatherer, detector)
        if llm_out:
            result["llm_sample"] = llm_out

    OUT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()