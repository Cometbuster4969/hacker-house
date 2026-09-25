"""Case memory — GraphRAG over closed investigations and over the agent's own cases.

Three retrieval paths, all bounded by ``as_of`` (only cases *closed* before the
investigation started can be used):

1. Graph hops: closed cases on the same customer, same cardholder (uid), same card, or
   whose episode used the same device profile (Customer/Card/Device -> Txn <- ClosedCase).
2. Case-based kNN: every closed case is embedded as the standardised behavioural
   vector of its episode (the same features the pattern model uses: channel mix,
   device novelty, region novelty, amounts, span ...). Nearest precedents are
   returned with their outcome, so "cases that looked like this were cleared as
   travel" becomes evidence. (The analyst notes are templated, so text embeddings
   carry little information; behaviour vectors do.)
3. Agent memory: every case this agent investigates is written back (JSON graph file,
   and TigerGraph when configured) with its cards, devices and verdict, so a later
   investigation can find an earlier one (policy 3a: "a case that names a device
   becomes evidence for the next analyst").
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config
from .patterns import FEATURES, episode_features

INDEX_PATH = config.MODELS_DIR / "case_index.parquet"
AGENT_MEMORY = config.MEMORY_DIR / "agent_cases.json"


def build_case_index(df: pd.DataFrame, closed: pd.DataFrame) -> pd.DataFrame:
    by_id = df.set_index("TransactionID")
    cust_rows = df.groupby("customer_id").indices
    recs = []
    for r in closed.itertuples():
        ids = [t for t in r.txn_ids.split("|") if t in by_id.index]
        ep = by_id.loc[ids].reset_index().sort_values("ts")
        cr = df.iloc[cust_rows[r.customer_id]]
        hist = cr[(cr.ts < ep.ts.min()) & (cr.ts >= ep.ts.min() - pd.Timedelta(days=90))]
        f = episode_features(ep, hist)
        f.update({"case_id": r.case_id, "outcome": r.outcome, "pattern": r.pattern,
                  "closed_at": r.closed_at, "customer_id": r.customer_id, "card_id": r.card_id,
                  "uids": "|".join(sorted(set(ep.uid))),
                  "devices": "|".join(sorted(set(ep.device_profile_id.dropna()))),
                  "p_flag": float(ep.p_txn.max()) if "p_txn" in ep else np.nan})
        recs.append(f)
    idx = pd.DataFrame(recs)
    idx.to_parquet(INDEX_PATH)
    return idx


@dataclass
class Precedent:
    case_id: str
    outcome: str
    pattern: str
    distance: float
    link: str = ""          # graph link type if found through a graph hop


class CaseMemory:
    def __init__(self, closed: pd.DataFrame, persist: bool = True):
        self.persist = persist
        self.closed = closed
        self.idx = pd.read_parquet(INDEX_PATH)
        X = self.idx[FEATURES].astype(float)
        self.mu, self.sd = X.mean(), X.std().replace(0, 1)
        self.Z = ((X - self.mu) / self.sd).clip(-5, 5).values
        self.calls = 0
        self.agent_cases: list[dict] = []

    # ---- 1. graph hops ------------------------------------------------------
    def graph_links(self, customer_id: str, uids: set, cards: set, devices: set, as_of) -> list[Precedent]:
        self.calls += 1
        i = self.idx[self.idx.closed_at < as_of]
        out = []
        for r in i.itertuples():
            link = ""
            if uids and set(r.uids.split("|")) & uids:
                link = "same cardholder (uid)"
            elif r.card_id in cards:
                link = "same card"
            elif devices and r.devices and set(r.devices.split("|")) & devices:
                link = "same device profile"
            elif r.customer_id == customer_id:
                link = "same customer"
            if link:
                out.append(Precedent(r.case_id, r.outcome, r.pattern, 0.0, link))
        rank = {"same cardholder (uid)": 0, "same device profile": 1, "same card": 2, "same customer": 3}
        out.sort(key=lambda p: (rank[p.link], p.case_id))
        return out

    # ---- 2. behavioural kNN --------------------------------------------------
    def knn(self, feats: dict, as_of, k: int = 5, outcome: str | None = None,
            pattern: str | None = None) -> list[Precedent]:
        self.calls += 1
        m = (self.idx.closed_at < as_of).values.copy()
        if outcome:
            m &= (self.idx.outcome == outcome).values
        if pattern:
            m &= (self.idx.pattern == pattern).values
        if not m.any():
            return []
        q = ((pd.Series(feats)[FEATURES].astype(float) - self.mu) / self.sd).clip(-5, 5).values
        d = np.sqrt(((self.Z[m] - q) ** 2).sum(1))
        sub = self.idx[m]
        order = np.argsort(d)[:k]
        return [Precedent(sub.iloc[j].case_id, sub.iloc[j].outcome, sub.iloc[j].pattern, float(d[j])) for j in order]

    # ---- 3. agent memory -----------------------------------------------------
    def agent_links(self, cards: set, devices: set, customer_id: str) -> list[dict]:
        self.calls += 1
        out = []
        for c in self.agent_cases:
            why = []
            if set(c["cards"]) & cards:
                why.append("card " + ", ".join(sorted(set(c["cards"]) & cards)[:3]))
            if devices and set(c["devices"]) & devices:   # caller passes hardware-specific profiles only
                why.append("device profile")
            if c["customer_id"] == customer_id:
                why.append("same customer")
            if why:
                out.append({**c, "why": "; ".join(why)})
        return out

    def write(self, record: dict):
        self.agent_cases.append(record)
        if not self.persist:
            return
        config.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        AGENT_MEMORY.write_text(json.dumps(self.agent_cases, indent=1, default=str))
