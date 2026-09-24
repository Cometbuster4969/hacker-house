"""Auth scheme tests for TigerGraph Savanna."""
from pathlib import Path
import requests
import json
import pyTigerGraph as tg

env = {}
for line in Path(".env").read_text(encoding="utf-8").splitlines():
    if line.strip() and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

host = env["TIGERGRAPH_HOST"]
gsql_token = env.get("TIGERGRAPH_TOKEN", "")
secret = env.get("TIGERGRAPH_SECRET", "")
DQ = '"'

print("=== Test 1: GSQL INSERT with old token ===")
try:
    conn = tg.TigerGraphConnection(host=host, graphname="FraudInvestigation", apiToken=gsql_token)
    stmt = "USE GRAPH FraudInvestigation\nINSERT INTO Customer VALUES (" + DQ + "TEST_C009" + DQ + ")"
    r = conn.gsql(stmt)
    print(str(r)[:300])
except Exception as e:
    print("failed:", str(e)[:200])

print("=== Test 2: REST++ Basic(user:token) ===")
upsert = json.dumps({"vertices": {"Customer": {"TEST_C001": {"customer_id": {"value": "TEST_C001"}}}}})
url = host + "/restpp/graph/FraudInvestigation"
for user in ["tigergraph", "anyone", gsql_token]:
    r = requests.post(url, data=upsert, auth=(user, gsql_token), timeout=10)
    print(user[:12], "->", r.status_code, r.text[:90])

print("=== Test 3: REST++ Basic(token:secret) ===")
r = requests.post(url, data=upsert, auth=(gsql_token, secret), timeout=10)
print("->", r.status_code, r.text[:90])

print("=== Test 4: read back via GSQL ===")
try:
    r = conn.gsql("USE GRAPH FraudInvestigation\nSELECT * FROM Customer WHERE primary_id==" + DQ + "TEST_C009" + DQ)
    print(str(r)[:300])
except Exception as e:
    print("failed:", str(e)[:200])