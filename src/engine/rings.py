"""Shared-origin / ring detection (policy R6, R9).

Lessons from both earlier solutions: linking cards on *generic* elements (e-mail
domain 'gmail.com', a missing e-mail 'nan', or 'Windows|UNK|chrome 62.0|UNK')
manufactured 2,000-card "rings" in every case. A link is admitted only when the shared
element is specific enough to identify an actor:

* a device profile that is hardware-specific (model + OS + screen known), or any
  profile whose use is anomalous (marked New for the account and/or behind a proxy on
  most rows), used by >=3 different customers inside the window; or
* the same undocumented modus operandi (sub-threshold structuring) repeated on other
  customers' cards in the window.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .patterns import detect_structuring
from .store import InvestigationStore, is_specific_device

RING_WINDOW_DAYS = 30
MIN_CUSTOMERS = 3


@dataclass
class Ring:
    kind: str                       # "device" | "structuring"
    element: str                    # the named shared element
    cards: list = field(default_factory=list)
    customers: list = field(default_factory=list)
    txn_ids: list = field(default_factory=list)
    anomaly: str = ""
    linked_closed_cases: list = field(default_factory=list)
    mean_p: float = 0.0


def device_ring(store: InvestigationStore, profile: str, as_of, scored: pd.Series | None = None,
                exclude_customer: str | None = None) -> Ring | None:
    if not isinstance(profile, str) or not profile:
        return None
    start = as_of - pd.Timedelta(days=RING_WINDOW_DAYS)
    nb = store.device_neighbors(profile, start, as_of)
    if nb.empty:
        return None
    anomalous = (nb.id_15.eq("New") & nb.id_23.notna()).mean()
    specific = is_specific_device(profile)
    if not (specific or anomalous >= 0.8):
        return None
    custs = sorted(set(nb.customer_id))
    if len(custs) < MIN_CUSTOMERS:
        return None
    # A popular phone model is not a ring: require the shared use to look like an actor —
    # anomalous on most rows (New device + proxy), or high model fraud probability.
    mean_p = float(scored.reindex(nb.TransactionID).mean()) if scored is not None else 0.0
    if anomalous < 0.8 and mean_p < 0.5:
        return None
    # closed cases whose episode used this profile (graph hop ClosedCase->Txn->Device)
    cc = store.closed
    linked = []
    ids_all = store.device_neighbors(profile, as_of - pd.Timedelta(days=150), as_of)
    idset = set(ids_all.TransactionID)
    for r in cc[(cc.outcome == "confirmed_fraud") & (cc.closed_at < as_of)].itertuples():
        if idset.intersection(r.txn_ids.split("|")):
            linked.append(r.case_id)
    anomaly = []
    if nb.id_15.eq("New").mean() >= 0.8:
        anomaly.append("marked New for every account")
    prox = nb.id_23.dropna()
    if len(prox) and len(prox) >= 0.8 * len(nb):
        anomaly.append(f"behind a proxy ({prox.mode().iloc[0].replace('IP_PROXY:', '').lower()})")
    return Ring("device", profile, sorted(set(nb.card_id)), custs, list(nb.TransactionID),
                ", ".join(anomaly), linked, mean_p)


def structuring_ring(store: InvestigationStore, as_of, exclude_card: str | None = None) -> Ring | None:
    """Other customers' cards showing the same sub-$500 structuring burst in the window."""
    start = as_of - pd.Timedelta(days=RING_WINDOW_DAYS)
    w = store.window(start, as_of)
    w = w[(w.channel == "online") & (w.TransactionAmt < 500) & (w.TransactionAmt >= 425)]
    counts = w.groupby("card_id").size()
    cards, ids = [], []
    for card in counts[counts >= 3].index:
        hit = detect_structuring(w[w.card_id == card])
        if hit:
            cards.append(card)
            ids += list(hit["rows"].TransactionID)
    if exclude_card:
        others = [c for c in cards if c != exclude_card]
    else:
        others = cards
    if not others:
        return None
    cc = store.closed
    linked = list(cc[(cc.pattern == "undocumented") & cc.analyst_notes.str.contains("just under \\$500")
                     & (cc.closed_at < as_of)].case_id)
    return Ring("structuring", "sub-$500 online purchase bursts", sorted(set(cards)),
                sorted({c.split("-")[0] for c in cards}), ids, "each purchase 85-100% of a $500 threshold",
                linked)
