"""Test TigerGraph upsert methods."""
import pyTigerGraph as tg
import os
from pathlib import Path
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
host = os.environ.get("TIGERGRAPH_HOST", "")
token = os.environ.get("TIGERGRAPH_API_TOKEN", "")
graph = os.environ.get("TIGERGRAPH_GRAPH", "FraudInvestigation")
print(f"Host: {host}")
print(f"Token: {token[:10]}...")
print(f"Graph: {graph}")
conn = tg.TigerGraphConnection(host=host, graphname=graph, apiToken=token)
# Test 1: REST API upsertVertex
print("\n--- Test 1: upsertVertex ---")
try:
    result = conn.upsertVertex("Customer", "TEST_C001", {"customer_id": "TEST_C001"})
    print(f"SUCCESS: {result}")
except Exception as e:
    print(f"FAILED: {e}")
# Test 2: GSQL INSERT via gsql endpoint
print("\n--- Test 2: GSQL INSERT ---")
try:
    result = conn.gsql(
        "USE GRAPH FraudInvestigation\n"
        'INSERT INTO Customer VALUES ("TEST_C002")',
        options={"graphName": graph}
    )
    print(f"SUCCESS: {result}")
except Exception as e:
    print(f"FAILED: {e}")
# Test 3: Raw REST call
print("\n--- Test 3: Raw REST POST ---")
import requests
try:
    url = f"{host}:9000/graph/{graph}/vertices/Customer/TEST_C003"
    payload = {"attributes": {"customer_id": "TEST_C003"}}
    resp = requests.post(url, json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    print(f"Status: {resp.status_code}")
    print(f"Body: {resp.text[:200]}")
except Exception as e:
    print(f"FAILED: {e}")
# Test 4: Check if endpoints are accessible
print("\n--- Test 4: Endpoint check ---")
for port_name, port in [("REST++", 9000), ("GSQL", 14240), ("GUI", 14240)]:
    try:
        url = f"{host}:{port}/echo"
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        print(f"{port_name}:{port} -> {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        print(f"{port_name}:{port} -> ERROR: {e}")
print("\n--- Done ---")

