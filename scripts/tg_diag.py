"""Diagnose query install state on Savanna."""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

import pyTigerGraph as tg
conn = tg.TigerGraphConnection(
    host=os.environ["TIGERGRAPH_HOST"],
    graphname="FraudInvestigation",
    username=os.environ.get("TIGERGRAPH_USER", ""),
    password=os.environ.get("TIGERGRAPH_SECRET", ""),
)

print("=== SHOW QUERY ===")
print(str(conn.gsql("USE GRAPH FraudInvestigation\nSHOW QUERY *"))[:800])

print("=== INSTALL graph_stats ===")
print(str(conn.gsql("USE GRAPH FraudInvestigation\nINSTALL QUERY graph_stats"))[:400])

print("=== RUN graph_stats ===")
print(str(conn.gsql("USE GRAPH FraudInvestigation\nRUN QUERY graph_stats()"))[:500])