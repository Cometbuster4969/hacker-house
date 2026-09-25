"""Episode reconstruction: which transactions belong to the same fraud episode?

Measured on the 4,665 confirmed closed cases:
  * an episode spans the customer's cards (7.9% of episode rows are on a sibling card),
  * consecutive fraud rows are <= 48h apart (p99 within-case gap 46h; between cases >48h),
  * exposure == sum(|amount|) of the rows, first_fraud_txn == earliest row (100%).

So: score every customer transaction in the look-back window with the calibrated model,
keep rows above the membership threshold, and take the 48h-gap chain that contains the
flagged transaction. Everything is bounded by ``as_of`` (the case's opened_at).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import config

MEMBER_THRESHOLD = 0.35   # Sep-Oct replay: mean Jaccard 0.838 (best 0.846 at 0.20); 0.35 keeps fewer false members
LOOKBACK_DAYS = 10


@dataclass
class Episode:
    txns: pd.DataFrame                      # rows in the episode (sorted by ts)
    candidates: pd.DataFrame                # scored look-back window
    threshold: float
    notes: list = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return list(self.txns.TransactionID)

    @property
    def exposure(self) -> float:
        return round(float(self.txns.TransactionAmt.abs().sum()), 2)

    @property
    def first_id(self) -> str:
        return self.txns.TransactionID.iloc[0] if len(self.txns) else ""


def chain(cands: pd.DataFrame, anchor_ts, gap_h: float = config.EPISODE_GAP_H) -> pd.DataFrame:
    """48h-gap chain of rows reachable from anchor_ts (both directions)."""
    if cands.empty:
        return cands
    c = cands.sort_values("ts")
    gap = pd.Timedelta(hours=gap_h)
    ts = list(c.ts)
    # find nearest position to anchor
    pos = min(range(len(ts)), key=lambda i: abs(ts[i] - anchor_ts))
    if abs(ts[pos] - anchor_ts) > gap:
        return c.iloc[0:0]
    lo = hi = pos
    while lo > 0 and ts[lo] - ts[lo - 1] <= gap:
        lo -= 1
    while hi < len(ts) - 1 and ts[hi + 1] - ts[hi] <= gap:
        hi += 1
    return c.iloc[lo:hi + 1]


def reconstruct(window: pd.DataFrame, flagged_id: str, flagged_ts, threshold: float = MEMBER_THRESHOLD,
                include_flagged: bool = True) -> Episode:
    """window: the customer's scored rows in [flagged_ts - LOOKBACK, as_of) with column p_txn."""
    members = window[window.p_txn >= threshold]
    if include_flagged and flagged_id not in set(members.TransactionID):
        members = pd.concat([members, window[window.TransactionID == flagged_id]])
    ep = chain(members, flagged_ts)
    if include_flagged and flagged_id not in set(ep.TransactionID):
        ep = window[window.TransactionID == flagged_id]
    return Episode(ep.sort_values("ts"), window, threshold)
