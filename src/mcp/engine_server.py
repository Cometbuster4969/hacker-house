"""MCP server (Model Context Protocol, stdio) exposing the investigation engine's tools.

Any MCP client (Claude Desktop, an IDE agent, the official tigergraph-mcp alongside it)
can drive an investigation step by step with the same as_of-bounded tools the agent
uses, or ask the pure policy engine what the rules require for a given case state.

    python -m src.mcp.engine_server          # stdio transport
"""
from __future__ import annotations

import json
from functools import lru_cache

import pandas as pd
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("hhgoa-fraud-engine")


@lru_cache(maxsize=1)
def _store():
    from src.engine.runner import load_store
    return load_store()


@lru_cache(maxsize=1)
def _memory():
    from src.engine.memory import CaseMemory
    return CaseMemory(_store().closed, persist=False)


def _rows(df: pd.DataFrame, limit: int = 50) -> list[dict]:
    cols = ["TransactionID", "ts", "card_id", "channel", "ProductCD", "TransactionAmt", "addr1", "risk_score",
            "p_txn", "id_15", "id_23", "device_profile_id"]
    out = df[cols].head(limit).copy()
    out["ts"] = out.ts.astype(str)
    return json.loads(out.to_json(orient="records"))


@mcp.tool()
def txn_context(txn_id: str) -> dict:
    """Attributes of one transaction, including the calibrated model score p_txn."""
    return _rows(_store().by_id.loc[[txn_id]])[0]


@mcp.tool()
def card_window(customer_id: str, start: str, end: str) -> list[dict]:
    """Every transaction on any of the customer's cards in [start, end) (ISO timestamps)."""
    return _rows(_store().customer_window(customer_id, pd.Timestamp(start), pd.Timestamp(end)), 200)


@mcp.tool()
def device_neighbors(device_profile: str, as_of: str, days: int = 30) -> dict:
    """Cards/customers that used a device profile in the `days` before as_of, with anomaly shares."""
    end = pd.Timestamp(as_of)
    nb = _store().device_neighbors(device_profile, end - pd.Timedelta(days=days), end)
    return {"n_txns": len(nb), "cards": sorted(set(nb.card_id)), "customers": sorted(set(nb.customer_id)),
            "share_marked_new": round(float(nb.id_15.eq("New").mean()), 3) if len(nb) else 0,
            "share_behind_proxy": round(float(nb.id_23.notna().mean()), 3) if len(nb) else 0}


@mcp.tool()
def prior_cases(customer_id: str, as_of: str) -> list[dict]:
    """Closed cases of this customer that were closed before as_of."""
    pc = _store().prior_cases(customer_id, pd.Timestamp(as_of))
    return json.loads(pc[["case_id", "outcome", "pattern", "exposure_usd", "closed_at"]].astype(str).to_json(orient="records"))


@mcp.tool()
def structuring_sweep(as_of: str, days: int = 30) -> list[dict]:
    """Cards with a sub-$500 online purchase burst (the undocumented structuring typology) in the window."""
    from src.engine.rings import structuring_ring
    r = structuring_ring(_store(), pd.Timestamp(as_of))
    return [] if r is None else [{"cards": r.cards, "precedents": r.linked_closed_cases, "txn_ids": r.txn_ids}]


@mcp.tool()
def investigate(case_id: str) -> dict:
    """Run the full agent on a case-pack case and return the answer file (does not write to disk)."""
    from src.engine.investigator import Investigator
    from src.engine.store import load_case_pack
    cp = load_case_pack().set_index("case_id")
    row = cp.loc[case_id].copy()
    row["case_id"] = case_id
    ans, trace = Investigator(_store(), _memory()).investigate(row)
    return {"answer": ans, "trace_steps": trace["steps"]}


@mcp.tool()
def policy_decide(p: float, verdict: str, exposure: float, pattern: str = "none", n_signals: int = 1,
                  trigger: str = "risk_score", response: str | None = None, shared_origin: str = "",
                  connected_cards: int = 0, cross_customer: bool = False) -> dict:
    """Ask Fraud Policy v1.0 (pure function) which actions and routes a case state requires."""
    from src.engine.policy import PolicyInput, decide
    d = decide(PolicyInput(p=p, verdict=verdict, exposure=exposure, pattern=pattern, n_signals=n_signals,
                           trigger=trigger, response=response, shared_origin=shared_origin,
                           connected_cards=connected_cards, cross_customer=cross_customer))
    return {"actions": d.actions, "file_sar": d.file_sar, "sar_reason": d.sar_reason}


if __name__ == "__main__":
    mcp.run()
