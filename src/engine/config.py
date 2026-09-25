"""Paths and thresholds. Every policy threshold quotes the policy line it encodes."""
from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data" / "HHGOA_IEEE"))
PREPARED_DIR = Path(os.getenv("PREPARED_DIR", DATA_DIR / "prepared"))
STORE_DIR = Path(os.getenv("STORE_DIR", ROOT / "data" / "store"))
MODELS_DIR = Path(os.getenv("MODELS_DIR", ROOT / "models"))
CASES_DIR = Path(os.getenv("CASES_DIR", ROOT / "cases"))
TRACES_DIR = Path(os.getenv("TRACES_DIR", ROOT / "traces"))
MEMORY_DIR = Path(os.getenv("MEMORY_DIR", ROOT / "memory"))
BENCH_DIR = Path(os.getenv("BENCH_DIR", ROOT / "benchmark"))

# --- Fraud Policy v1.0 thresholds -----------------------------------------
P_OPEN_CASE = 0.30        # 3a "Open one whenever fraud probability reaches 0.30"      (>=)
P_VERIFY_BELOW = 0.70     # R1 "assessed fraud probability is below 0.70"              (<)
SAR_EXPOSURE = 1000.0     # 3a/R2 "exposure exceeds $1,000"                            (>)
ESCALATE_EXPOSURE = 500.0 # R4/R8 "exposure exceeds $500"                              (>)
R5_CLEARED = 100.0        # R5 "If a purchase over $100 has already cleared"           (>)
STOP_HIGH = 0.85          # 6 "at or above 0.85"                                       (>=)
STOP_LOW = 0.15           # 6 "at or below 0.15"                                       (<=)
BLOCK_L1_MAX = 2500.0     # 2 "BLOCK_CARD when exposure <= $2,500" -> L1, else L2

# --- Engine settings (learned / tuned on closed-case backtest) --------------
EPISODE_GAP_H = 48.0      # closed cases chain fraud txns with gaps <= 48h (measured: p99 within-case gap 46h)
BENCH_PRIOR = 0.50        # dataset README: "Half the cases are legitimate"
MAX_EVIDENCE_ROUNDS = 2
