"""The engine MCP server exposes its tools and the policy tool answers without data."""
import asyncio
import json

import pytest

pytest.importorskip("mcp.server.fastmcp", reason="needs the tigergraph extra: mcp>=1.2,<2")
from src.mcp.engine_server import mcp  # noqa: E402


def test_tools_listed():
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"txn_context", "card_window", "device_neighbors", "prior_cases", "structuring_sweep",
            "investigate", "policy_decide"} <= names


def test_policy_tool():
    out = asyncio.run(mcp.call_tool("policy_decide", {"p": 0.9, "verdict": "fraud", "exposure": 3000,
                                                       "n_signals": 3, "response": "denied"}))
    content = out[0] if isinstance(out, tuple) else out
    res = json.loads(content[0].text)
    acts = [a["action"] for a in res["actions"]]
    assert "BLOCK_CARD" in acts and "FILE_REPORT" in acts
    assert [a["route"] for a in res["actions"] if a["action"] == "BLOCK_CARD"] == ["L2"]
