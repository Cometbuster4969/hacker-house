#!/usr/bin/env python3
"""Final round: replace 5 multi-hop drafts with single-hop queries,
then smoke-run all installed queries and write evidence JSON."""
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
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

GRAPH = "FraudInvestigation"

BROKEN = ["txn_context", "device_fanout", "region_activity",
          "device_ring", "sibling_case_search"]

QUERIES = [
    (
        "txn_devices",
        """CREATE QUERY txn_devices(VERTEX<Transaction> t) FOR GRAPH FraudInvestigation {
  seed = {t};
  devices = SELECT d1 FROM seed:s-(FROM_DEVICE:e1)-DeviceProfile:d1;
  PRINT devices;
}""",
    ),
    (
        "txn_emails",
        """CREATE QUERY txn_emails(VERTEX<Transaction> t) FOR GRAPH FraudInvestigation {
  seed = {t};
  p = SELECT p1 FROM seed:s-(PURCHASER_EMAIL:e1)-EmailDomain:p1;
  r = SELECT r1 FROM seed:s-(RECIPIENT_EMAIL:e2)-EmailDomain:r1;
  PRINT p, r;
}""",
    ),
    (
        "device_txns",
        """CREATE QUERY device_txns(VERTEX<DeviceProfile> d) FOR GRAPH FraudInvestigation {
  SumAccum<INT> @@n_txns;
  txns = SELECT x2 FROM Transaction:x2-(FROM_DEVICE:e1)-DeviceProfile:y2 WHERE y2 == d ACCUM @@n_txns += 1;
  PRINT @@n_txns;
}""",
    ),
    (
        "region_txns",
        """CREATE QUERY region_txns(VERTEX<BillingRegion> r) FOR GRAPH FraudInvestigation {
  SumAccum<INT> @@n_txns;
  txns = SELECT x2 FROM Transaction:x2-(BILLED_IN:e1)-BillingRegion:y2 WHERE y2 == r ACCUM @@n_txns += 1;
  PRINT @@n_txns;
}""",
    ),
    (
        "card_txns",
        """CREATE QUERY card_txns(VERTEX<Card> c) FOR GRAPH FraudInvestigation {
  SumAccum<INT> @@n_txns;
  seed = {c};
  txns = SELECT x2 FROM seed:s-(MADE:e1)-Transaction:x2 ACCUM @@n_txns += 1;
  PRINT @@n_txns;
}""",
    ),
]

FAIL = ("Encountered", "Semantic Check Fails", "Semantic Check Error",
        "Syntax Error", "Type Check Error", "Cannot invoke",
        "type/semantic error", "installation failed")


def main():
    import pyTigerGraph as tg
    conn = tg.TigerGraphConnection(
        host=os.environ["TIGERGRAPH_HOST"], graphname=GRAPH,
        username=os.environ.get("TIGERGRAPH_USER", ""),
        password=os.environ.get("TIGERGRAPH_SECRET", ""),
    )
    print(f"Connected: {conn.echo()}")

    for name in BROKEN:
        conn.gsql(f"USE GRAPH {GRAPH}\nDROP QUERY {name}")
        print(f"  dropped draft {name}")
    time.sleep(2)

    for name, gsql in QUERIES:
        conn.gsql(f"USE GRAPH {GRAPH}\nDROP QUERY {name}")
        time.sleep(1)
        out = str(conn.gsql(f"USE GRAPH {GRAPH}\n{gsql}"))
        if any(m in out for m in FAIL):
            print(f"  CREATE {name}: FAIL -> {out.splitlines()[:4]}")
            continue
        print(f"  CREATE {name}: OK")
        time.sleep(1)
        out = str(conn.gsql(f"USE GRAPH {GRAPH}\nINSTALL QUERY {name}"))
        if any(m in out for m in FAIL):
            print(f"  INSTALL {name}: FAIL -> {out.splitlines()[:4]}")
        else:
            print(f"  INSTALL {name}: OK")

    data_dir = Path(__file__).parent.parent / "data" / "HHGOA_IEEE"
    first = None
    with open(data_dir / "transactions.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("TransactionID") and row.get("customer_id"):
                first = row
                break
    txn_id = first["TransactionID"]
    cust = first["customer_id"]
    card = f"{cust}-K1"
    region = str(int(float(first.get("addr1") or 444)))

    device_id = None
    with open(data_dir / "identity.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("TransactionID"):
                key = "|".join([row.get("DeviceInfo", ""), row.get("id_30", ""),
                                row.get("id_31", ""), row.get("id_33", "")])
                device_id = f"D{hashlib.md5(key.encode()).hexdigest()[:6].upper()}"
                break

    print(f"\nSmoke-running installed queries (txn={txn_id}, card={card}, "
          f"device={device_id}, region={region})")

    runs = [
        ("graph_stats", "RUN QUERY graph_stats()"),
        ("card_activity", f'RUN QUERY card_activity("{card}")'),
        ("card_txns", f'RUN QUERY card_txns("{card}")'),
        ("customer_cards", f'RUN QUERY customer_cards("{cust}")'),
        ("card_case_history", f'RUN QUERY card_case_history("{card}")'),
        ("txn_devices", f'RUN QUERY txn_devices("{txn_id}")'),
        ("txn_emails", f'RUN QUERY txn_emails("{txn_id}")'),
        ("device_txns", f'RUN QUERY device_txns("{device_id}")'),
        ("region_txns", f'RUN QUERY region_txns("{region}")'),
        ("fraud_pagerank(GDS)", "RUN QUERY fraud_pagerank(0.001, 10, 10)"),
    ]

    results = {}
    ok = 0
    for name, stmt in runs:
        t0 = time.time()
        out = str(conn.gsql(f"USE GRAPH {GRAPH}\n{stmt}"))
        dt = round(time.time() - t0, 2)
        failed = any(m in out for m in FAIL)
        results[name] = {"status": "error" if failed else "ok",
                         "latency_s": dt, "output": out[:2000]}
        if not failed:
            ok += 1
        print(f"[{'ERR' if failed else 'OK '}] {name} ({dt}s): "
              f"{out.replace(chr(10), ' ')[:100]}")

    out_path = Path(__file__).parent.parent / "cases" / "tigergraph_query_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "host": os.environ["TIGERGRAPH_HOST"],
        "graph": GRAPH,
        "dataset_provenance": ("HHGOA_IEEE (IEEE-CIS-derived dataset provided by "
                               "HHGOA organizers); first-10K subset + full identity/"
                               "closed-cases loaded into TigerGraph"),
        "input_ids": {"transaction": txn_id, "customer": cust, "card": card,
                      "device": device_id, "region": region},
        "queries_ok": ok, "queries_total": len(runs),
        "results": results,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\n{ok}/{len(runs)} live queries succeeded. Evidence: {out_path}")


if __name__ == "__main__":
    main()