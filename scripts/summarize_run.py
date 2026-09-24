#!/usr/bin/env python3
"""Summarize a completed 20-case investigation run.

Reads cases/HHG-*.json (CaseAnswer files) and produces:
  - per-case table: verdict, probability, pattern, SAR, exposure, tokens, latency
  - verdict distribution + probability stats
  - answer-schema validation (required fields, ID presence)
  - cases/run_summary.json for the submission artefacts

Usage:  python scripts/summarize_run.py
"""
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
CASES_DIR = ROOT / "cases"
OUT_PATH = CASES_DIR / "run_summary.json"

REQUIRED_TOP = ["case_id", "case"]
REQUIRED_CASE = ["verdict", "fraud_probability", "pattern"]


def load_answers():
    answers = []
    problems = []
    for p in sorted(CASES_DIR.glob("HHG-*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            problems.append(f"{p.name}: unparseable JSON ({e})")
            continue
        missing = [k for k in REQUIRED_TOP if k not in data]
        if missing:
            problems.append(f"{p.name}: missing top-level {missing}")
            continue
        case = data.get("case") or {}
        missing = [k for k in REQUIRED_CASE if k not in case]
        if missing:
            problems.append(f"{p.name}: missing case.{missing}")
        answers.append(data)
    return answers, problems


def main():
    answers, problems = load_answers()
    if problems:
        print("Schema problems:")
        for p in problems:
            print(f"  - {p}")
    if not answers:
        print("No answer files found in cases/ — run `python main.py investigate` first.")
        sys.exit(1)

    rows = []
    for a in answers:
        c = a.get("case") or {}
        sar = a.get("sar") or {}
        rows.append({
            "case_id": a.get("case_id", "?"),
            "verdict": c.get("verdict", "?"),
            "prob": float(c.get("fraud_probability", 0) or 0),
            "pattern": c.get("pattern", "none"),
            "exposure": float(c.get("exposure_usd", 0) or 0),
            "sar": bool(sar.get("file", False)),
            "tool_calls": int(a.get("tool_calls", 0) or 0),
            "tokens": int(a.get("tokens", 0) or 0),
            "latency_s": float(a.get("latency_s", 0) or 0),
            "stop_reason": (a.get("stop_reason") or "")[:60],
        })

    tally = {"fraud": 0, "legitimate": 0, "uncertain": 0}
    for r in rows:
        v = r["verdict"].lower()
        tally[v] = tally.get(v, 0) + 1

    probs = [r["prob"] for r in rows]
    total_tokens = sum(r["tokens"] for r in rows)

    from collections import Counter
    counts = Counter(r["tokens"] for r in rows)
    # A "frozen counter" is only suspicious if one value REPEATS across cases
    # (with all-distinct counts the mode is meaningless). 0 tokens always flags.
    frozen = counts.most_common(1)[0][0] if counts and \
        counts.most_common(1)[0][1] >= 2 else None
    suspects = [r["case_id"] for r in rows
                if r["tokens"] == 0 or (frozen is not None and r["tokens"] == frozen)]

    summary = {
        "n_cases": len(rows),
        "verdict_tally": tally,
        "fraud_share": round(tally.get("fraud", 0) / len(rows), 3),
        "uncertain_share": round(tally.get("uncertain", 0) / len(rows), 3),
        "sars_filed": sum(1 for r in rows if r["sar"]),
        "prob_min": round(min(probs), 3),
        "prob_max": round(max(probs), 3),
        "prob_mean": round(statistics.mean(probs), 3),
        "total_tokens": total_tokens,
        "suspect_cases_no_llm": suspects,
        "total_tool_calls": sum(r["tool_calls"] for r in rows),
        "total_latency_s": round(sum(r["latency_s"] for r in rows), 1),
        "schema_problems": problems,
        "rows": rows,
    }

    hdr = (f"{'case':8} {'verdict':11} {'prob':>5} {'pattern':28} "
           f"{'exposure':>9} {'SAR':>4} {'tok':>7} {'sec':>5}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['case_id']:8} {r['verdict']:11} {r['prob']:5.2f} {r['pattern']:28} "
              f"{r['exposure']:9.2f} {str(r['sar']):>5} {r['tokens']:7} {r['latency_s']:5.1f}")
    print("-" * len(hdr))
    print(f"Verdicts: {tally}  |  SARs: {summary['sars_filed']}  "
          f"|  tokens: {summary['total_tokens']:,}")
    print(f"Uncertain share: {summary['uncertain_share']:.0%} (target <= 30%)")
    print(f"\nWrote {OUT_PATH}")
    OUT_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()