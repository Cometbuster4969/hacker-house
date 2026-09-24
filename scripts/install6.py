import os, sys, time
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
conn = tg.TigerGraphConnection(host=os.environ["TIGERGRAPH_HOST"], graphname="FraudInvestigation", username=os.environ.get("TIGERGRAPH_USER",""), password=os.environ.get("TIGERGRAPH_SECRET",""))
names = ["txn_context","card_case_history","device_fanout","region_activity","device_ring","sibling_case_search"]
for n in names:
    out = conn.gsql(f"USE GRAPH FraudInvestigation\nINSTALL QUERY {n}")
    s = str(out).replace("\n", " ")
    print(f"{n}: {s[-220:]}")
    time.sleep(2)
print("Waiting 90s for compilation to finish...")
time.sleep(90)
for n in names:
    out = conn.gsql(f'USE GRAPH FraudInvestigation\nRUN QUERY {n}' + ('("3000001")' if n=="txn_context" else '("C06075-K1")' if n in ("card_case_history","sibling_case_search") else '("DA39719")' if n in ("device_fanout","device_ring") else '("444")'))
    s = str(out).replace("\n", " ")
    status = "ERR" if "Semantic Check Fails" in s or "Encountered" in s else "OK"
    print(f"[{status}] {n}: {s[:150]}")