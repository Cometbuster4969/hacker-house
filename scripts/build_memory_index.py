#!/usr/bin/env python3
"""Embed every closed case (behavioural episode vector) -> models/case_index.parquet (~7 min)."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.engine import config  # noqa: E402
from src.engine.memory import build_case_index  # noqa: E402
from src.engine.store import load_closed_cases  # noqa: E402

idx = build_case_index(pd.read_parquet(config.STORE_DIR / "scored.parquet"), load_closed_cases())
print(idx.shape, "->", config.MODELS_DIR / "case_index.parquet")
