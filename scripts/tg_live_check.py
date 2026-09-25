#!/usr/bin/env python3
"""Live TigerGraph check + engine write-back.

Reads TIGERGRAPH_* from .env and:
  1. connects (API token, then GSQL secret, then user/password),
  2. prints vertex/edge counts,
  3. installs tigergraph/queries/engine_queries.gsql (``--install``),
  4. re-runs the 20 cases so each is upserted as an InvestigationCase vertex
     (``--write``); written_to_graph becomes true only on an acknowledged write.

Output: evidence/tg_live_check.json
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import src.utils.config  # noqa: E402,F401  (loads .env)
from src.graph.tigergraph_client import create_tigergraph_client  # noqa: E402

out = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "connected": False}
cli = create_tigergraph_client()
out["host"] = cli.host
if not cli.is_connected():
    out["error"] = "could not connect (check network egress, host and credentials)"
else:
    out["connected"] = True
    out["stats"] = cli.get_graph_stats()
    if "--install" in sys.argv:
        gsql = (ROOT / "tigergraph/queries/engine_queries.gsql").read_text()
        res = cli.conn.gsql(f"USE GRAPH {cli.graph}\n{gsql}\nINSTALL QUERY ALL")
        out["install"] = str(res)[-2000:]
    if "--write" in sys.argv:
        from src.engine.runner import run
        answers = run()
        out["written_to_graph"] = sum(a["case"]["written_to_graph"] for a in answers)
(ROOT / "evidence").mkdir(exist_ok=True)
(ROOT / "evidence/tg_live_check.json").write_text(json.dumps(out, indent=2, default=str))
print(json.dumps({k: v for k, v in out.items() if k != "install"}, indent=2, default=str))
sys.exit(0 if out["connected"] else 1)
