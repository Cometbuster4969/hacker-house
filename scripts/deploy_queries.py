#!/usr/bin/env python3
"""Deploy 10 fraud GSQL queries + PageRank to Savanna (v3)."""
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

QUERIES = [
    (
        "txn_context",
        """CREATE QUERY txn_context(VERTEX<Transaction> t) FOR GRAPH FraudInvestigation {
  seed = {t};
  cards = SELECT tgt FROM Card:tgt-(MADE:e1)-seed:s;
  customers = SELECT s2 FROM Customer:s2-(OWNS:e2)-cards:c3;
  devices = SELECT tgt FROM seed:s-(FROM_DEVICE:e3)-DeviceProfile:tgt;
  pemail = SELECT tgt FROM seed:s-(PURCHASER_EMAIL:e4)-EmailDomain:tgt;
  remail = SELECT tgt FROM seed:s-(RECIPIENT_EMAIL:e5)-EmailDomain:tgt;
  region = SELECT tgt FROM seed:s-(BILLED_IN:e6)-BillingRegion:tgt;
  PRINT cards, customers, devices, pemail, remail, region;
}""",
    ),
    (
        "card_activity",
        """CREATE QUERY card_activity(VERTEX<Card> c) FOR GRAPH FraudInvestigation {
  SumAccum<INT> @@n_txns;
  SetAccum<VERTEX<DeviceProfile>> @@devices;
  seed = {c};
  txns = SELECT tgt FROM seed:s-(MADE:e1)-Transaction:tgt ACCUM @@n_txns += 1;
  dv = SELECT d2 FROM txns:t1-(FROM_DEVICE:e2)-DeviceProfile:d2 ACCUM @@devices += d2;
  PRINT @@n_txns, @@devices;
}""",
    ),
    (
        "device_fanout",
        """CREATE QUERY device_fanout(VERTEX<DeviceProfile> d) FOR GRAPH FraudInvestigation {
  SumAccum<INT> @@n_txns;
  SetAccum<VERTEX<Card>> @@cards;
  seed = {d};
  txns = SELECT s FROM Transaction:s-(FROM_DEVICE:e1)-seed:x ACCUM @@n_txns += 1;
  cards = SELECT c2 FROM txns:t1-(MADE:e2)-Card:c2 ACCUM @@cards += c2;
  PRINT @@n_txns, @@cards;
}""",
    ),
    (
        "card_case_history",
        """CREATE QUERY card_case_history(VERTEX<Card> c) FOR GRAPH FraudInvestigation {
  seed = {c};
  cases = SELECT s FROM ClosedCase:s-(ON_CARD:e1)-seed:x;
  PRINT cases;
}""",
    ),
    (
        "customer_cards",
        """CREATE QUERY customer_cards(VERTEX<Customer> cust) FOR GRAPH FraudInvestigation {
  SumAccum<INT> @@n_cards;
  seed = {cust};
  cards = SELECT tgt FROM seed:s-(OWNS:e1)-Card:tgt ACCUM @@n_cards += 1;
  PRINT @@n_cards, cards;
}""",
    ),
    (
        "email_domain_activity",
        """CREATE QUERY email_domain_activity(VERTEX<EmailDomain> e) FOR GRAPH FraudInvestigation {
  SumAccum<INT> @@n_purchaser;
  SumAccum<INT> @@n_recipient;
  p = SELECT s FROM Transaction:s-(PURCHASER_EMAIL:ep)-EmailDomain:e2 WHERE e2 == e ACCUM @@n_purchaser += 1;
  r = SELECT s FROM Transaction:s-(RECIPIENT_EMAIL:er)-EmailDomain:e2 WHERE e2 == e ACCUM @@n_recipient += 1;
  PRINT @@n_purchaser, @@n_recipient;
}""",
    ),
    (
        "region_activity",
        """CREATE QUERY region_activity(VERTEX<BillingRegion> r) FOR GRAPH FraudInvestigation {
  SumAccum<INT> @@n_txns;
  SetAccum<VERTEX<Card>> @@cards;
  seed = {r};
  txns = SELECT s FROM Transaction:s-(BILLED_IN:e1)-seed:x ACCUM @@n_txns += 1;
  cards = SELECT c2 FROM txns:t1-(MADE:e2)-Card:c2 ACCUM @@cards += c2;
  PRINT @@n_txns, @@cards;
}""",
    ),
    (
        "device_ring",
        """CREATE QUERY device_ring(VERTEX<DeviceProfile> d) FOR GRAPH FraudInvestigation {
  seed = {d};
  cards1 = SELECT c1 FROM Card:c1-(MADE:e1)-Transaction:t1-(FROM_DEVICE:e2)-seed:x;
  devices2 = SELECT d3 FROM cards1:c2-(MADE:e3)-Transaction:t2-(FROM_DEVICE:e4)-DeviceProfile:d3;
  PRINT cards1, devices2;
}""",
    ),
    (
        "sibling_case_search",
        """CREATE QUERY sibling_case_search(VERTEX<Card> c) FOR GRAPH FraudInvestigation {
  SetAccum<VERTEX<ClosedCase>> @@cases;
  seed = {c};
  devices = SELECT d2 FROM seed:s-(MADE:e1)-Transaction:t1-(FROM_DEVICE:e2)-DeviceProfile:d2;
  sib_cards = SELECT c2 FROM devices:d3-(FROM_DEVICE:e3)-Transaction:t2-(MADE:e4)-Card:c2;
  cases = SELECT cc FROM sib_cards:c3-(ON_CARD:e5)-ClosedCase:cc ACCUM @@cases += cc;
  PRINT @@cases;
}""",
    ),
    (
        "graph_stats",
        """CREATE QUERY graph_stats() FOR GRAPH FraudInvestigation {
  SumAccum<INT> @@customers;
  SumAccum<INT> @@cards;
  SumAccum<INT> @@txns;
  SumAccum<INT> @@devices;
  SumAccum<INT> @@emails;
  SumAccum<INT> @@regions;
  SumAccum<INT> @@cases;
  c1 = SELECT s FROM Customer:s ACCUM @@customers += 1;
  c2 = SELECT s FROM Card:s ACCUM @@cards += 1;
  c3 = SELECT s FROM Transaction:s ACCUM @@txns += 1;
  c4 = SELECT s FROM DeviceProfile:s ACCUM @@devices += 1;
  c5 = SELECT s FROM EmailDomain:s ACCUM @@emails += 1;
  c6 = SELECT s FROM BillingRegion:s ACCUM @@regions += 1;
  c7 = SELECT s FROM ClosedCase:s ACCUM @@cases += 1;
  PRINT @@customers, @@cards, @@txns, @@devices, @@emails, @@regions, @@cases;
}""",
    ),
    (
        "fraud_pagerank",
        """CREATE QUERY fraud_pagerank(FLOAT max_change, INT max_iter, INT top_k) FOR GRAPH FraudInvestigation {
  FLOAT damping = 0.85;
  SumAccum<FLOAT> @@max_diff = 999;
  SumAccum<FLOAT> @received_score;
  SumAccum<FLOAT> @score;
  SumAccum<FLOAT> @prev_score;
  SumAccum<INT> @out_degree;
  Start = {Card.*};
  init = SELECT s FROM Start:s ACCUM s.@score = 1.0, s.@prev_score = 0.0;
  deg = SELECT s FROM Start:s -(MADE:e1)- Transaction:t ACCUM s.@out_degree += 1;
  WHILE @@max_diff > max_change LIMIT max_iter DO
    @@max_diff = 0;
    scored = SELECT t FROM Start:s -(MADE:e2)- Transaction:t
             WHERE s.@out_degree > 0
             ACCUM t.@received_score += s.@score / s.@out_degree
             POST-ACCUM t.@prev_score = t.@score,
                        t.@score = (1.0 - damping) + damping * t.@received_score,
                        t.@received_score = 0,
                        @@max_diff += abs(t.@score - t.@prev_score);
  END;
  top = SELECT s FROM Start:s ORDER BY s.@score DESC LIMIT top_k;
  PRINT top[top.@score];
}""",
    ),
]

CREATE_FAIL = ("Encountered", "Semantic Check Fails", "Semantic Check Error",
               "Syntax Error", "Cannot invoke")


def create_failed(out) -> bool:
    return any(m in str(out) for m in CREATE_FAIL)


def install_failed(out) -> bool:
    s = str(out)
    if "installed already" in s:
        return False
    if "Successfully installed" in s:
        return False
    if any(m in s for m in CREATE_FAIL):
        return True
    return False


def main():
    host = os.getenv("TIGERGRAPH_HOST", "")
    user = os.getenv("TIGERGRAPH_USER", "")
    secret = os.getenv("TIGERGRAPH_SECRET", "")

    import pyTigerGraph as tg
    conn = tg.TigerGraphConnection(host=host, graphname=GRAPH,
                                   username=user, password=secret)
    print(f"Connected: {conn.echo()}")

    created, installed = [], []
    for name, gsql in QUERIES:
        try:
            conn.gsql(f"USE GRAPH {GRAPH}\nDROP QUERY {name}")
            time.sleep(1)
        except Exception:
            pass

        out = conn.gsql(f"USE GRAPH {GRAPH}\n{gsql}")
        if create_failed(out):
            print(f"  CREATE {name}: FAIL -> {str(out).splitlines()[:4]}")
            continue
        print(f"  CREATE {name}: OK")
        created.append(name)

        time.sleep(1)
        out = conn.gsql(f"USE GRAPH {GRAPH}\nINSTALL QUERY {name}")
        if install_failed(out):
            print(f"  INSTALL {name}: FAIL -> {str(out).splitlines()[:4]}")
        else:
            print(f"  INSTALL {name}: OK")
            installed.append(name)

    print()
    print(f"Created {len(created)}/{len(QUERIES)}, installed {len(installed)}/{len(QUERIES)}")

    print()
    print("=" * 60)
    print("Smoke tests")
    print("=" * 60)
    for label, stmt in [
        ("graph_stats", f"USE GRAPH {GRAPH}\nRUN QUERY graph_stats()"),
        ("fraud_pagerank", f"USE GRAPH {GRAPH}\nRUN QUERY fraud_pagerank(0.001, 10, 10)"),
    ]:
        try:
            out = conn.gsql(stmt)
            if create_failed(out):
                print(f"[FAIL] {label}: {str(out)[:200]}")
            else:
                print(f"[OK] {label}: {str(out)[:300]}")
        except Exception as e:
            print(f"[ERR] {label}: {str(e)[:200]}")


if __name__ == "__main__":
    main()