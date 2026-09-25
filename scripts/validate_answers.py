#!/usr/bin/env python3
"""Validate the answer files in cases/ against the README contract and Fraud Policy v1.0.

Two tiers, so the graded artifact is verifiable on a clean machine with no dataset download:

  tier A  contract + policy + id shapes + closed-case references. Needs only the two csv files
          checked into data/HHGOA_IEEE/. This is what CI (tests/test_answers.py) runs.
  tier B  additionally resolves every transaction/card id against the built store and re-adds the
          exposure from the raw amounts. Runs automatically once `python main.py build` exists.

Exit code is non-zero on any violation, or on a file count other than 20.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.engine import config  # noqa: E402
from src.engine.store import load_case_pack, load_closed_cases  # noqa: E402
from src.engine.validate import N_CHECKS, validate_dir  # noqa: E402

d = Path(sys.argv[1]) if len(sys.argv) > 1 else config.CASES_DIR

store = None
if (config.STORE_DIR / "scored.parquet").exists():
    from src.engine.runner import load_store  # noqa: E402
    store = load_store()

res = validate_dir(d, store, load_case_pack(), set(load_closed_cases().case_id))
bad = sum(len(v) for v in res.values())
for k, v in res.items():
    for x in v:
        print(f"{k}: {x}")
if store is not None:
    note = f"tier A+B, {N_CHECKS} mechanical checks, ids and exposure resolved against the store"
else:
    note = (f"tier A only, {N_CHECKS} mechanical checks; run `python main.py build` to also "
            f"resolve every id and re-add exposure from the raw amounts")
print(f"{len(res)} files, {bad} violations  [{note}]")
sys.exit(1 if bad or len(res) != 20 else 0)
