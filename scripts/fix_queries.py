#!/usr/bin/env python3
"""Fix 6 draft queries: patterns must follow schema edge direction (forward only)."""
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

QUERIES = [
    (
        "txn_context",
        """CREATE QUERY txn_context(VERTEX<Transaction> t) FOR GRAPH FraudInvestigation {
  seed = {t};
  cards = SELECT c4 FROM Card:c4-(MADE:e1)-Transaction:x4 WHERE x4 == t;
  customers = SELECT s2 FROM Customer:s2-(OWNS:e2)-Card:c5-(MADE:e7)-Transaction:x5 WHERE x5 == t;
  devices = SELECT d4 FROM seed:s-(FROM_DEVICE:e3)-DeviceProfile:d4;
  pemail = SELECT p4 FROM seed:s-(PURCHASER_EMAIL:e4)-EmailDomain:p4;
  remail = SELECT r4 FROM seed:s-(RECIPIENT_EMAIL:e5)-EmailDomain:r4;
  region = SELECT g4 FROM seed:s-(BILLED_IN:e6)-BillingRegion:g4;
  PRINT cards, customers, devices, pemail, remail, region;
}""",
    ),
    (
        "card_case_history",
        """CREATE QUERY card_case_history(VERTEX<Card> c) FOR GRAPH FraudInvestigation {
  cases = SELECT cc FROM ClosedCase:cc-(ON_CARD:e1)-Card:x1 WHERE x1 == c;
  PRINT cases;
}""",
    ),
    (
        "device_fanout",
        """CREATE QUERY device_fanout(VERTEX<DeviceProfile> d) FOR GRAPH FraudInvestigation {
  SumAccum<INT> @@n_txns;
  SetAccum<VERTEX<Card>> @@cards;
  txns = SELECT x2 FROM Transaction:x2-(FROM_DEVICE:e1)-DeviceProfile:y2 WHERE y2 == d ACCUM @@n_txns += 1;
  cards = SELECT c2 FROM Card:c2-(MADE:e2)-Transaction:t1-(FROM_DEVICE:e3)-DeviceProfile:y3 WHERE y3 == d ACCUM @@cards += c2;
  PRINT @@n_txns, @@cards;
}""",
    ),
    (
        "region_activity",
        """CREATE QUERY region_activity(VERTEX<BillingRegion> r) FOR GRAPH FraudInvestigation {
  SumAccum<INT> @@n_txns;
  SetAccum<VERTEX<Card>> @@cards;
  txns = SELECT x2 FROM Transaction:x2-(BILLED_IN:e1)-BillingRegion:y2 WHERE y2 == r ACCUM @@n_txns += 1;
  cards = SELECT c2 FROM Card:c2-(MADE:e2)-Transaction:t1-(BILLED_IN:e3)-BillingRegion:y3 WHERE y3 == r ACCUM @@cards += c2;
  PRINT @@n_txns, @@cards;
}""",
    ),
    (
        "device_ring",
        """CREATE QUERY device_ring(VERTEX<DeviceProfile> d) FOR GRAPH FraudInvestigation {
  cards1 = SELECT c1 FROM Card:c1-(MADE:e1)-Transaction:t1-(FROM_DEVICE:e2)-DeviceProfile:y1 WHERE y1 == d;
  txns2 = SELECT t2 FROM cards1:c2-(MADE:e3)-Transaction:t2;
  devices2 = SELECT d3 FROM txns2:t3-(FROM_DEVICE:e4)-DeviceProfile:d3;
  PRINT cards1, devices2;
}""",
    ),
    (
        "sibling_case_search",
        """CREATE QUERY sibling_case_search(VERTEX<Card> c) FOR GRAPH FraudInvestigation {
  SetAccum<VERTEX<DeviceProfile>> @@my_devices;
  SetAccum<VERTEX<ClosedCase>> @@cases;
  seed = {c};
  dstep = SELECT d2 FROM seed:x-(MADE:e1)-Transaction:t1-(FROM_DEVICE:e2)-DeviceProfile:d2 ACCUM @@my_devices += d2;
  sib_txns = SELECT t3 FROM Transaction:t3-(FROM_DEVICE:e3)-DeviceProfile:d3 WHERE @@my_devices.contains(d3);
  sib_cards = SELECT c2 FROM Card:c2-(MADE:e4)-sib_txns:t4;
  cases = SELECT cc FROM ClosedCase:cc-(ON_CARD:e5)-Card:c6 WHERE c6 IN sib_cards ACCUM @@cases += cc;
  PRINT @@cases;
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
    for name, gsql in QUERIES:
        conn.gsql(f"USE GRAPH {GRAPH}\nDROP QUERY {name}")
        time.sleep(1)
        out = str(conn.gsql(f"USE GRAPH {GRAPH}\n{gsql}"))
        if any(m in out for m in FAIL):
            print(f"  CREATE {name}: FAIL -> {out.splitlines()[:5]}")
            continue
        print(f"  CREATE {name}: OK")
        time.sleep(1)
        out = str(conn.gsql(f"USE GRAPH {GRAPH}\nINSTALL QUERY {name}"))
        if any(m in out for m in FAIL):
            print(f"  INSTALL {name}: FAIL -> {out.splitlines()[:5]}")
        else:
            print(f"  INSTALL {name}: OK")


if __name__ == "__main__":
    main()