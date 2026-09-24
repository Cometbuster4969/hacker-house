#!/usr/bin/env python3
"""
Generate synthetic demo data for testing the fraud investigation agent
when the real HHGOA_IEEE dataset is not available.
Creates sample transactions, identity records, closed cases, and the case pack.
"""
import csv
import os
import random
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)

DATA_DIR = Path(__file__).parent.parent / "data" / "HHGOA_IEEE"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Config
NUM_CUSTOMERS = 500
NUM_TRANSACTIONS = 10000
NUM_IDENTITY_RECORDS = 3000
NUM_CLOSED_CASES = 200
CARD_NETWORKS = ["visa", "mastercard", "american express", "discover"]
CARD_TYPES = ["credit", "debit"]
PRODUCT_CODES = ["W", "C", "H", "R", "S"]
EMAIL_DOMAINS = ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com"]
DEVICE_INFOS = [
    "SAMSUNG SM-G935F Build/NRD90M",
    "Apple iPhone 11",
    "SAMSUNG SM-G892A Build/NRD90M",
    "Google Pixel 3",
    "Huawei P30",
    "Xiaomi Mi 9",
    "LG-H870",
    "Nokia 7 Plus",
]
OPERATING_SYSTEMS = ["Android 7.0", "Android 8.0", "Android 9.0", "iOS 13.0", "iOS 14.0", "Windows 10"]
BROWSERS = ["samsung browser 6.2", "chrome 78.0", "mobile safari", "chrome for android", "firefox 70.0"]
SCREENS = ["2220x1080", "1920x1080", "2560x1440", "1334x750", "2436x1125"]
REGIONS = [float(r) for r in range(100, 600, 10)]
COUNTRY_CODES = [87.0, 1.0, 44.0, 49.0, 33.0]

# Generate customer IDs
CUSTOMERS = [f"C{i:05d}" for i in range(1, NUM_CUSTOMERS + 1)]


def random_ts(start=None, end=None):
    """Generate a random timestamp between start and end."""
    if start is None:
        start = datetime(2016, 7, 2)
    if end is None:
        end = datetime(2016, 12, 31)
    delta = end - start
    random_seconds = random.randint(0, int(delta.total_seconds()))
    return start + timedelta(seconds=random_seconds)


def generate_card_combos(customer_id):
    """Generate card column combinations for a customer."""
    num_cards = random.randint(1, 3)
    cards = []
    for i in range(num_cards):
        cards.append({
            "card1": f"CRD{random.randint(1000, 9999)}",
            "card2": f"{random.randint(100, 999)}",
            "card3": round(random.uniform(100, 200), 1),
            "card4": random.choice(CARD_NETWORKS),
            "card5": round(random.uniform(100, 200), 1),
            "card6": random.choice(CARD_TYPES),
        })
    return cards


def generate_transactions():
    """Generate synthetic transaction data."""
    print("Generating transactions...")

    # Pre-generate card combos per customer
    customer_cards = {}
    for cust in CUSTOMERS:
        customer_cards[cust] = generate_card_combos(cust)

    rows = []
    for i in range(NUM_TRANSACTIONS):
        txn_id = f"T{i+1:07d}"
        customer_id = random.choice(CUSTOMERS)
        cards = customer_cards[customer_id]
        card = random.choice(cards)

        channel = "online" if random.random() > 0.3 else "in_person"
        product_cd = random.choice(PRODUCT_CODES) if channel == "online" else "W"
        amount = round(random.lognormvariate(3.5, 1.2), 2)
        amount = min(amount, 5000.0)
        ts = random_ts()
        risk_score = round(random.betavariate(2, 5), 4)

        # Some transactions are "fraudulent-looking"
        if random.random() < 0.02:
            risk_score = round(random.uniform(0.7, 0.95), 4)
            amount = round(random.uniform(50, 2000), 2)

        row = {
            "TransactionID": txn_id,
            "TransactionDT": int(ts.timestamp()),
            "TransactionAmt": amount,
            "ProductCD": product_cd,
            "card1": card["card1"],
            "card2": card["card2"],
            "card3": card["card3"],
            "card4": card["card4"],
            "card5": card["card5"],
            "card6": card["card6"],
            "addr1": random.choice(REGIONS),
            "addr2": random.choice(COUNTRY_CODES),
            "dist1": round(random.uniform(0, 100), 1) if random.random() > 0.3 else "",
            "dist2": round(random.uniform(0, 50), 1) if random.random() > 0.5 else "",
            "P_emaildomain": random.choice(EMAIL_DOMAINS) if channel == "online" else "",
            "R_emaildomain": random.choice(EMAIL_DOMAINS) if channel == "online" and random.random() > 0.3 else "",
        }
        # Add some V columns (sparse)
        for v in range(1, 340):
            if random.random() > 0.7:
                row[f"V{v}"] = round(random.uniform(0, 10), 2)

        row["customer_id"] = customer_id
        row["ts"] = ts.strftime("%Y-%m-%d %H:%M:%S")
        row["channel"] = channel
        row["risk_score"] = risk_score

        rows.append(row)

    # Sort by timestamp
    rows.sort(key=lambda r: r["ts"])

    # Write CSV - only include core columns (skip V columns for manageable file size)
    core_fields = ["TransactionID", "TransactionDT", "TransactionAmt", "ProductCD",
                   "card1", "card2", "card3", "card4", "card5", "card6",
                   "addr1", "addr2", "dist1", "dist2",
                   "P_emaildomain", "R_emaildomain",
                   "customer_id", "ts", "channel", "risk_score"]
    with open(DATA_DIR / "transactions.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=core_fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)

    print(f"  Written {len(rows)} transactions")
    return rows


def generate_identity(transactions):
    """Generate identity records for online transactions."""
    print("Generating identity records...")

    online_txns = [t for t in transactions if t["channel"] == "online"]
    sample = random.sample(online_txns, min(NUM_IDENTITY_RECORDS, len(online_txns)))

    rows = []
    for txn in sample:
        device_info = random.choice(DEVICE_INFOS)
        device_type = "mobile" if "SAMSUNG" in device_info or "iPhone" in device_info else "desktop"

        row = {
            "TransactionID": txn["TransactionID"],
            "DeviceType": device_type,
            "DeviceInfo": device_info,
        }
        # Add id columns
        for i in range(1, 12):
            row[f"id_{i:02d}"] = round(random.uniform(-5, 5), 2)

        row["id_12"] = random.choice(["New", "Found", ""])
        row["id_13"] = str(random.randint(10, 50))
        row["id_15"] = random.choice(["New", "Found"])
        row["id_23"] = random.choice(["transparent", "anonymous", "hidden", ""])
        row["id_30"] = random.choice(OPERATING_SYSTEMS)
        row["id_31"] = random.choice(BROWSERS)
        row["id_33"] = random.choice(SCREENS)
        row["id_34"] = random.choice(["match", "no_match", ""])
        for i in range(35, 39):
            row[f"id_{i}"] = random.choice(["T", "F", ""])

        rows.append(row)

    fieldnames = ["TransactionID", "DeviceType", "DeviceInfo"] + \
                 [f"id_{i:02d}" for i in range(1, 12)] + \
                 [f"id_{i}" for i in range(12, 39)]

    with open(DATA_DIR / "identity.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"  Written {len(rows)} identity records")


def generate_closed_cases(transactions):
    """Generate closed case history."""
    print("Generating closed cases...")

    patterns = ["card_testing", "card_not_present_fraud", "card_not_present_new_device",
                "out_of_region_use", "account_takeover", "undocumented", "none"]
    outcomes = ["confirmed_fraud"] * 8 + ["cleared"] * 2  # 80/20 split

    rows = []
    for i in range(NUM_CLOSED_CASES):
        case_id = f"CC-{i+1:04d}"
        customer_id = random.choice(CUSTOMERS)
        outcome = random.choice(outcomes)
        pattern = random.choice(patterns[:-1]) if outcome == "confirmed_fraud" else "none"

        opened = random_ts(datetime(2016, 7, 1), datetime(2016, 10, 31))
        closed = opened + timedelta(days=random.randint(1, 30))

        # Pick some transactions for this case
        cust_txns = [t for t in transactions if t["customer_id"] == customer_id]
        n_txns = min(random.randint(1, 5), len(cust_txns))
        selected_txns = random.sample(cust_txns, n_txns) if cust_txns else []

        exposure = sum(t["TransactionAmt"] for t in selected_txns)

        rows.append({
            "case_id": case_id,
            "customer_id": customer_id,
            "card_id": f"{customer_id}-K1",
            "opened_at": opened.strftime("%Y-%m-%d %H:%M:%S"),
            "closed_at": closed.strftime("%Y-%m-%d %H:%M:%S"),
            "outcome": outcome,
            "pattern": pattern,
            "first_fraud_txn_id": selected_txns[0]["TransactionID"] if selected_txns else "",
            "txn_ids": "|".join(t["TransactionID"] for t in selected_txns),
            "n_txns": n_txns,
            "exposure_usd": round(exposure, 2),
            "connected_card_ids": f"{customer_id}-K2" if random.random() > 0.7 else "",
            "actions_taken": "BLOCK_CARD|CREATE_CASE" if outcome == "confirmed_fraud" else "CLOSE_NO_FRAUD",
            "report_filed": "true" if outcome == "confirmed_fraud" and exposure > 1000 else "false",
            "analyst_notes": f"{'Confirmed fraud' if outcome == 'confirmed_fraud' else 'False alarm'}: {pattern}",
        })

    with open(DATA_DIR / "closed_cases_history.csv", "w", newline="", encoding="utf-8") as f:
        fieldnames = ["case_id", "customer_id", "card_id", "opened_at", "closed_at",
                      "outcome", "pattern", "first_fraud_txn_id", "txn_ids", "n_txns",
                      "exposure_usd", "connected_card_ids", "actions_taken", "report_filed",
                      "analyst_notes"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"  Written {len(rows)} closed cases")


def generate_case_pack(transactions):
    """Generate the case pack (20 benchmark cases)."""
    print("Generating case pack...")

    triggers = [
        ("HHG-001", "2016-12-05 01:55:28", "risk_score", 0.61,
         "Real-time model scored transaction {txn} (${amt}, in billing region {region}) at 0.61. Review and decide."),
        ("HHG-002", "2016-11-22 23:27:07", "risk_score", 0.79,
         "Real-time model scored transaction {txn} (${amt}, online) at 0.79. Review and decide."),
        ("HHG-003", "2016-12-10 15:01:21", "customer_report", None,
         "Customer {cust} message: 'I never made this ${amt} purchase. Please check my card.' Refers to {txn}."),
        ("HHG-004", "2016-12-29 07:53:54", "customer_report", None,
         "Customer {cust} message: 'I never made this ${amt} purchase. Please check my card.' Refers to {txn}."),
        ("HHG-005", "2016-12-08 03:38:37", "risk_score", 0.54,
         "Real-time model scored transaction {txn} (${amt}, online) at 0.54. Review and decide."),
    ]

    # Use actual transactions from the data
    high_risk = [t for t in transactions if t.get("risk_score", 0) > 0.5]
    online_txns = [t for t in transactions if t["channel"] == "online"]

    rows = []
    for case_id, opened, trigger_type, score, template in triggers:
        txn = random.choice(high_risk if score and score > 0.5 else online_txns)
        trigger_text = template.format(
            txn=txn["TransactionID"],
            amt=f"{txn['TransactionAmt']:.2f}",
            region=int(txn.get("addr1", 0)),
            cust=txn["customer_id"],
        )
        rows.append({
            "case_id": case_id,
            "opened_at": opened,
            "trigger_type": trigger_type,
            "trigger_text": trigger_text,
            "flagged_txn_id": txn["TransactionID"],
            "card_id": f"{txn['customer_id']}-K1",
            "customer_id": txn["customer_id"],
            "risk_score": score if score else "",
        })

    # Generate remaining 15 cases
    for i in range(6, 21):
        txn = random.choice(transactions)
        trigger_type = random.choice(["risk_score", "customer_report"])
        score = round(random.uniform(0.5, 0.95), 2) if trigger_type == "risk_score" else None
        opened = random_ts(datetime(2016, 11, 1), datetime(2016, 12, 31))

        if trigger_type == "customer_report":
            text = f"Customer {txn['customer_id']} message: 'I never made this ${txn['TransactionAmt']:.2f} purchase. Please check my card.' Refers to {txn['TransactionID']}."
        else:
            text = f"Real-time model scored transaction {txn['TransactionID']} (${txn['TransactionAmt']:.2f}, {txn['channel']}) at {score}. Review and decide."

        rows.append({
            "case_id": f"HHG-{i:03d}",
            "opened_at": opened.strftime("%Y-%m-%d %H:%M:%S"),
            "trigger_type": trigger_type,
            "trigger_text": text,
            "flagged_txn_id": txn["TransactionID"],
            "card_id": f"{txn['customer_id']}-K1",
            "customer_id": txn["customer_id"],
            "risk_score": score if score else "",
        })

    with open(DATA_DIR / "case_pack.csv", "w", newline="", encoding="utf-8") as f:
        fieldnames = ["case_id", "opened_at", "trigger_type", "trigger_text",
                      "flagged_txn_id", "card_id", "customer_id", "risk_score"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"  Written {len(rows)} case pack entries")


def main():
    # Safety guard: never silently overwrite the real HHGOA dataset.
    # The real closed_cases_history.csv is ~2.6 MB / 5,565 cases; the synthetic
    # one is much smaller. Require an explicit --force to overwrite anything.
    import sys
    existing = [
        f for f in ("transactions.csv", "identity.csv",
                    "closed_cases_history.csv", "case_pack.csv")
        if (DATA_DIR / f).exists()
    ]
    if existing and "--force" not in sys.argv:
        print("REFUSING to overwrite existing dataset files in "
              f"{DATA_DIR}: {', '.join(existing)}")
        print("These may be the REAL HHGOA benchmark files. This script")
        print("generates SYNTHETIC demo data for testing only.")
        print("Re-run with --force if you really want to replace them.")
        sys.exit(1)

    print("=== Generating Demo Dataset (SYNTHETIC — testing only) ===")
    print(f"Output: {DATA_DIR}")
    print()

    transactions = generate_transactions()
    generate_identity(transactions)
    generate_closed_cases(transactions)
    generate_case_pack(transactions)

    print()
    print("=== Demo Dataset Generated ===")
    print(f"Files in {DATA_DIR}:")
    for f in sorted(DATA_DIR.iterdir()):
        size = f.stat().st_size
        print(f"  {f.name}: {size:,} bytes")


if __name__ == "__main__":
    main()
