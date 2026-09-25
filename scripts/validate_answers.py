#!/usr/bin/env python3
"""Validate answer files against the README contract and Fraud Policy v1.0."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.engine import config  # noqa: E402
from src.engine.runner import load_store  # noqa: E402
from src.engine.store import load_case_pack  # noqa: E402
from src.engine.validate import validate_dir  # noqa: E402

d = Path(sys.argv[1]) if len(sys.argv) > 1 else config.CASES_DIR
res = validate_dir(d, load_store(), load_case_pack())
bad = 0
for k, v in res.items():
    for x in v:
        print(f"{k}: {x}")
    bad += len(v)
print(f"{len(res)} files, {bad} violations")
sys.exit(1 if bad or len(res) != 20 else 0)
