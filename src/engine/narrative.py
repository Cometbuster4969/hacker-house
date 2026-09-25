"""Deterministic natural-language rendering (summary, SAR narrative, pattern description).

Text is generated from the structured findings only, so every sentence is traceable to
evidence. An LLM can optionally rewrite the summary (src/agent/llm_reasoner.py) but
never touches IDs, amounts, verdicts or actions.
"""
from __future__ import annotations

import pandas as pd

from .store import device_label

PATTERN_TEXT = {
    "card_testing": "card testing",
    "card_not_present_fraud": "card-not-present fraud",
    "card_not_present_new_device": "card-not-present fraud from a new device",
    "out_of_region_use": "out-of-region use",
    "account_takeover": "account takeover",
    "undocumented": "an undocumented coordinated pattern",
    "none": "no fraud pattern",
}


def money(x: float) -> str:
    return f"${x:,.2f}"


def span_text(rows: pd.DataFrame) -> str:
    a, b = rows.ts.min(), rows.ts.max()
    if a == b:
        return f"on {a:%Y-%m-%d} at {a:%H:%M}"
    if a.date() == b.date():
        return f"on {a:%Y-%m-%d} between {a:%H:%M} and {b:%H:%M}"
    return f"between {a:%Y-%m-%d} and {b:%Y-%m-%d}"


def channel_mix(rows: pd.DataFrame) -> str:
    on = int((rows.channel == "online").sum())
    ip = len(rows) - on
    parts = []
    if on:
        parts.append(f"{on} online")
    if ip:
        parts.append(f"{ip} in-person")
    return " and ".join(parts)


def region_text(rows: pd.DataFrame) -> str:
    regs = sorted({str(int(x)) for x in rows.addr1.dropna()})
    return ("billing region " + ", ".join(regs)) if regs else "no billing region recorded"


def sar_narrative(f: dict) -> str:
    ep: pd.DataFrame = f["episode_rows"]
    cust, cards = f["customer_id"], sorted(set(ep.card_id))
    s = []
    s.append(f"Between {ep.ts.min():%Y-%m-%d %H:%M} and {ep.ts.max():%Y-%m-%d %H:%M}, "
             f"{len(ep)} transaction(s) totalling {money(f['exposure'])} were made on card(s) "
             f"{', '.join(cards)} belonging to customer {cust}.")
    s.append(f"The activity comprised {channel_mix(ep)} transaction(s) under product code(s) "
             f"{', '.join(sorted(set(ep.ProductCD)))}, in {region_text(ep)}.")
    devs = [d for d in ep.device_profile_id.dropna().unique()]
    if devs:
        s.append("Online transactions came from device profile(s) " + "; ".join(device_label(d) for d in devs[:3]) + ".")
    for line in f.get("sar_how", []):
        s.append(line)
    if f.get("response_text"):
        s.append(f["response_text"])
    for line in f.get("sar_why", []):
        s.append(line)
    s.append(f"Total suspicious amount: {money(f['exposure'])}. Actions recommended: "
             + ", ".join(a["action"] for a in f["final_actions"]) + ".")
    while len(s) < 6:
        s.append(f"The case was opened on {f['as_of']:%Y-%m-%d %H:%M} and all evidence predates that time.")
    return " ".join(s[:12])
