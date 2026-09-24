#!/usr/bin/env python3
"""Run all 11 installed GSQL queries LIVE and save evidence to
cases/tigergraph_query_results.json."""
import csv
import hashlib
import json
import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
GRAPH = "FraudInvestigation"
def derive_device_id(device_info, id_30, id_31, id_33):
    key = f"{device_info}|{id_30}|{id_31}|{id_33}"
    return f"D{hashlib.md5(key.encode()).hexdigest()[:6].upper()}"
def main():
    import pyTigerGraph as tg
    conn = tg.TigerGraphConnection(
        host=os.environ["TIGERGRAPH_HOST"], graphname=GRAPH,
        username=os.environ.get("TIGERGRAPH_USER", ""),
        password=os.environ.get("TIGERGRAPH_SECRET", ""),
    )
    print(f"Connected: {conn.echo()}")
    data_dir = Path(__file__).parent.parent / "data" / "HHGOA_IEEE"
    first_txn = None
    with open(data_dir / "transactions.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("TransactionID") and row.get("customer_id"):
                first_txn = row
                break
    device_id = None
    with open(data_dir / "identity.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("TransactionID"):
                device_id = derive_device_id(row.get("DeviceInfo", ""),
                                             row.get("id_30", ""),
                                             row.get("id_31", ""),
                                             row.get("id_33", ""))
                break
    txn_id = first_txn["TransactionID"]
    customer_id = first_txn["customer_id"]
    card_id = f"{customer_id}-K1"
    region_id = str(int(float(first_txn.get("addr1") or 0))) if first_txn.get("addr1") else ""
    email = first_txn.get("P_emaildomain", "") or ""
    print(f"Real IDs from loaded subset: txn={txn_id} card={card_id} device={device_id}")
    runs = [
        ("graph_stats", "RUN QUERY graph_stats()"),
        ("txn_context", f'RUN QUERY txn_context("{txn_id}")'),
        ("card_activity", f'RUN QUERY card_activity("{card_id}")'),
        ("card_case_history", f'RUN QUERY card_case_history("{card_id}")'),
        ("customer_cards", f'RUN QUERY customer_cards("{customer_id}")'),
        ("sibling_case_search", f'RUN QUERY sibling_case_search("{card_id}")'),
        ("device_fanout", f'RUN QUERY device_fanout("{device_id}")'),
        ("device_ring", f'RUN QUERY device_ring("{device_id}")'),
        ("region_activity", f'RUN QUERY region_activity("{region_id}")'),
        ("email_domain_activity", f'RUN QUERY email_domain_activity("{email}")'),
        ("fraud_pagerank(GDS)", "RUN QUERY fraud_pagerank(0.001, 10, 10)"),
    ]
    FAIL = ("Encountered", "Semantic Check Fails", "Semantic Check Error", "Syntax Error")
    results = {}
    ok = 0
    for name, stmt in runs:
        t0 = time.time()
        out = conn.gsql(f"USE GRAPH {GRAPH}\n{stmt}")
        dt = round(time.time() - t0, 2)
        failed = any(m in str(out) for m in FAIL)
        results[name] = {"status": "error" if failed else "ok",
                         "latency_s": dt, "output": str(out)[:2000]}
        if not failed:
            ok += 1
        tag = "ERR" if failed else "OK "
        print(f"[{tag}] {name} ({dt}s): {str(out).replace(chr(10), ' ')[:100]}")
    out_path = Path(__file__).parent.parent / "cases" / "tigergraph_query_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "host": os.environ["TIGERGRAPH_HOST"],
        "graph": GRAPH,
        "dataset_provenance": ("HHGOA_IEEE (IEEE-CIS-derived dataset provided by "
                               "HHGOA organizers); first-10K subset loaded into TigerGraph"),
        "input_ids": {"transaction": txn_id, "customer": customer_id,
                      "card": card_id, "device": device_id,
                      "region": region_id, "email_domain": email},
        "queries_ok": ok, "queries_total": len(runs),
        "results": results,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\n{ok}/{len(runs)} queries succeeded. Evidence saved: {out_path}")
if __name__ == "__main__":
    main()