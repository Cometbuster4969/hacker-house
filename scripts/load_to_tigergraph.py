#!/usr/bin/env python3
"""
Load the HHGOA_IEEE dataset into TigerGraph.
This is the critical step that connects the real data to the graph.

Usage:
    python scripts/load_to_tigergraph.py
    python scripts/load_to_tigergraph.py --max-txns 10000  # Load subset for testing

Requires:
1. TigerGraph schema deployed (run deploy_schema.py first)
2. Real dataset files in data/HHGOA_IEEE/
3. TIGERGRAPH_HOST and TIGERGRAPH_TOKEN in .env
"""
import csv
import hashlib
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()


def derive_device_id(device_info, id_30, id_31, id_33):
    key = f"{device_info}|{id_30}|{id_31}|{id_33}"
    return f"D{hashlib.md5(key.encode()).hexdigest()[:6].upper()}"


def safe_float(val):
    try:
        return float(val) if val else 0.0
    except (ValueError, TypeError):
        return 0.0


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-txns", type=int, default=0)
    args = parser.parse_args()

    data_dir = Path(__file__).parent.parent / "data" / "HHGOA_IEEE"
    host = os.getenv("TIGERGRAPH_HOST", "")
    token = os.getenv("TIGERGRAPH_TOKEN", "")
    graph = os.getenv("TIGERGRAPH_GRAPH", "FraudInvestigation")

    if not host:
        print("ERROR: Set TIGERGRAPH_HOST in .env")
        sys.exit(1)

    print(f"Loading data into TigerGraph at {host}...")
    print(f"Data directory: {data_dir}")

    try:
        import pyTigerGraph as tg
        if token:
            conn = tg.TigerGraphConnection(host=host, graphname=graph, apiToken=token)
        else:
            conn = tg.TigerGraphConnection(
                host=host, graphname=graph,
                username=os.getenv("TG_USERNAME", "tigergraph"),
                password=os.getenv("TG_PASSWORD", "tigergraph"),
            )
        conn.echo()
    except Exception as e:
        print(f"ERROR connecting to TigerGraph: {e}")
        sys.exit(1)

    # ---- Load Identity (build device profiles) ----
    print("\n1. Loading identity records and device profiles...")
    identity_path = data_dir / "identity.csv"
    identity_by_txn = {}

    if identity_path.exists():
        with open(identity_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            devices_seen = set()
            count = 0
            for row in reader:
                txn_id = row.get("TransactionID", "")
                if not txn_id:
                    continue

                device_info = row.get("DeviceInfo", "")
                id_30 = row.get("id_30", "")
                id_31 = row.get("id_31", "")
                id_33 = row.get("id_33", "")
                device_id = derive_device_id(device_info, id_30, id_31, id_33)

                identity_by_txn[txn_id] = {
                    "device_id": device_id,
                    "device_type": row.get("DeviceType", ""),
                    "id_15": row.get("id_15", ""),
                    "id_23": row.get("id_23", ""),
                }

                if device_id not in devices_seen:
                    devices_seen.add(device_id)
                    conn.upsertVertex("DeviceProfile", device_id, {
                        "device_info": device_info,
                        "device_type": row.get("DeviceType", ""),
                        "os": id_30,
                        "browser": id_31,
                        "screen": id_33,
                    })

                count += 1
                if count % 50000 == 0:
                    print(f"  {count} identity records...")
                    conn.commit()

            conn.commit()
            print(f"  Loaded {count} identity records, {len(devices_seen)} device profiles")
    else:
        print("  identity.csv not found, skipping")

    # ---- Load Closed Cases ----
    print("\n2. Loading closed cases...")
    cases_path = data_dir / "closed_cases_history.csv"
    if cases_path.exists():
        with open(cases_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            count = 0
            for row in reader:
                case_id = row.get("case_id", "")
                if not case_id:
                    continue
                conn.upsertVertex("ClosedCase", case_id, {
                    "customer_id": row.get("customer_id", ""),
                    "card_id": row.get("card_id", ""),
                    "opened_at": row.get("opened_at", ""),
                    "closed_at": row.get("closed_at", ""),
                    "outcome": row.get("outcome", ""),
                    "pattern": row.get("pattern", "none"),
                    "exposure_usd": safe_float(row.get("exposure_usd", "0")),
                    "n_txns": int(row.get("n_txns", "0") or "0"),
                    "analyst_notes": row.get("analyst_notes", "")[:500],
                })
                count += 1
            conn.commit()
            print(f"  Loaded {count} closed cases")
    else:
        print("  closed_cases_history.csv not found, skipping")

    # ---- Load Transactions (the big one) ----
    print("\n3. Loading transactions...")
    txn_path = data_dir / "transactions.csv"
    if not txn_path.exists():
        print("  ERROR: transactions.csv not found!")
        sys.exit(1)

    card_map = {}  # composite_key -> card_id
    customer_cards = defaultdict(set)
    email_domains = set()
    regions = set()

    with open(txn_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        count = 0
        batch_size = 1000

        for row in reader:
            txn_id = row.get("TransactionID", "")
            customer_id = row.get("customer_id", "")
            if not txn_id or not customer_id:
                continue

            # Derive card_id
            card_key = "|".join([
                row.get("card2", ""), row.get("card3", ""),
                row.get("card4", ""), row.get("card5", ""),
                row.get("card6", ""),
            ])
            composite = f"{customer_id}|{card_key}"

            if composite not in card_map:
                idx = len(customer_cards[customer_id]) + 1
                card_id = f"{customer_id}-K{idx}"
                card_map[composite] = card_id
                customer_cards[customer_id].add(card_id)

                conn.upsertVertex("Customer", customer_id, {})
                conn.upsertVertex("Card", card_id, {
                    "customer_id": customer_id,
                    "network": row.get("card4", ""),
                    "card_type": row.get("card6", ""),
                    "card1": row.get("card1", ""),
                })
                conn.upsertEdge("Customer", customer_id, "OWNS", "Card", card_id)

            card_id = card_map[composite]
            amount = safe_float(row.get("TransactionAmt", "0"))
            risk_score = safe_float(row.get("risk_score", "0"))
            addr1 = safe_float(row.get("addr1", "0"))
            channel = row.get("channel", "")

            conn.upsertVertex("Transaction", txn_id, {
                "customer_id": customer_id,
                "card_id": card_id,
                "amount": amount,
                "ts": row.get("ts", ""),
                "channel": channel,
                "risk_score": risk_score,
                "product_cd": row.get("ProductCD", ""),
                "addr1": addr1,
                "addr2": safe_float(row.get("addr2", "0")),
                "p_emaildomain": row.get("P_emaildomain", ""),
                "r_emaildomain": row.get("R_emaildomain", ""),
            })

            conn.upsertEdge("Card", card_id, "MADE", "Transaction", txn_id)

            # Billing region
            region_id = str(int(addr1)) if addr1 else "UNKNOWN"
            conn.upsertVertex("BillingRegion", region_id, {
                "country_code": safe_float(row.get("addr2", "0")),
            })
            conn.upsertEdge("Transaction", txn_id, "BILLED_IN", "BillingRegion", region_id)

            # Email domains
            p_email = row.get("P_emaildomain", "")
            if p_email and p_email not in email_domains:
                email_domains.add(p_email)
                conn.upsertVertex("EmailDomain", p_email, {"domain_type": "purchaser"})
            if p_email:
                conn.upsertEdge("Transaction", txn_id, "PURCHASER_EMAIL", "EmailDomain", p_email)

            r_email = row.get("R_emaildomain", "")
            if r_email and r_email not in email_domains:
                email_domains.add(r_email)
                conn.upsertVertex("EmailDomain", r_email, {"domain_type": "recipient"})
            if r_email:
                conn.upsertEdge("Transaction", txn_id, "RECIPIENT_EMAIL", "EmailDomain", r_email)

            # Device link
            identity = identity_by_txn.get(txn_id)
            if identity:
                device_id = identity["device_id"]
                conn.upsertEdge("Transaction", txn_id, "FROM_DEVICE", "DeviceProfile", device_id, {
                    "is_new_device": identity["id_15"].lower() == "new",
                    "proxy_type": identity["id_23"],
                })

            count += 1
            if count % batch_size == 0:
                conn.commit()
                print(f"  {count} transactions loaded...")
                if args.max_txns and count >= args.max_txns:
                    print(f"  Reached limit: {args.max_txns}")
                    break

        conn.commit()
        print(f"  Loaded {count} transactions total")

    # ---- Load Case Pack (just validate) ----
    print("\n4. Validating case pack...")
    case_pack_path = data_dir / "case_pack.csv"
    if case_pack_path.exists():
        with open(case_pack_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            cases = list(reader)
        print(f"  {len(cases)} cases in case pack")
        for c in cases:
            print(f"    {c['case_id']}: {c['trigger_type']} - {c['flagged_txn_id']}")

    # ---- Summary ----
    print("\n=== Data Loading Complete ===")
    print(f"Customers: {len(customer_cards)}")
    print(f"Cards: {len(card_map)}")
    print(f"Transactions: {count}")
    print(f"Device profiles: {len(identity_by_txn)}")
    print(f"Email domains: {len(email_domains)}")
    print(f"Case pack: {len(cases) if case_pack_path.exists() else 0}")


if __name__ == "__main__":
    main()
