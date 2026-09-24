#!/usr/bin/env python3
"""
Load HHGOA_IEEE data into TigerGraph (Savanna).

Auth: database user (TIGERGRAPH_USER / TIGERGRAPH_SECRET) - pyTigerGraph
mints a JWT internally. User must have the superuser role.

Writes: batched upsertData() REST++ payloads (one HTTP call per batch)
with automatic retries on transient network errors.

Usage:
    python scripts/load_to_tigergraph.py --max-txns 1000   # quick test
    python scripts/load_to_tigergraph.py --max-txns 10000
"""
import csv
import hashlib
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def safe_float(val):
    try:
        return float(val) if val else 0.0
    except (ValueError, TypeError):
        return 0.0


def derive_device_id(device_info, id_30, id_31, id_33):
    key = f"{device_info}|{id_30}|{id_31}|{id_33}"
    return f"D{hashlib.md5(key.encode()).hexdigest()[:6].upper()}"


class BatchUploader:
    """Accumulates vertex/edge upserts, flushes as REST++ upsertData batches."""

    def __init__(self, conn, batch_size=2000, verbose=True):
        self.conn = conn
        self.batch_size = batch_size
        self.verbose = verbose
        self.reset()
        self.total_flushed = 0
        self.retries = 0

    def reset(self):
        self.vertices = defaultdict(set)      # vtype -> {id}
        # from_type -> from_id -> etype -> to_type -> {to_id}
        self.edges = defaultdict(dict)
        self.pending = 0

    def vertex(self, vtype, vid):
        self.vertices[vtype].add(vid)
        self.pending += 1
        if self.pending >= self.batch_size:
            self.flush()

    def edge(self, from_type, from_id, etype, to_type, to_id):
        self.edges[from_type].setdefault(from_id, {}).setdefault(
            etype, {}).setdefault(to_type, set()).add(to_id)
        self.pending += 1
        if self.pending >= self.batch_size:
            self.flush()

    def _build_payload(self):
        payload = {}
        if any(self.vertices.values()):
            payload["vertices"] = {
                vt: {vid: {} for vid in ids}
                for vt, ids in self.vertices.items() if ids
            }
        if self.edges:
            ep = {}
            for from_type, by_from in self.edges.items():
                for from_id, by_etype in by_from.items():
                    node = {}
                    for etype, by_totype in by_etype.items():
                        node[etype] = {
                            to_type: {tid: {} for tid in to_ids}
                            for to_type, to_ids in by_totype.items()
                        }
                    ep.setdefault(from_type, {})[from_id] = node
            payload["edges"] = ep
        return payload

    def flush(self):
        if self.pending == 0:
            return
        payload = self._build_payload()
        n = self.pending

        for attempt in range(6):
            try:
                self.conn.upsertData(payload)
                break
            except Exception as e:
                self.retries += 1
                wait = min(2 ** attempt * 2, 30)
                msg = str(e)[:100]
                print(f"    retry {attempt + 1}/6 in {wait}s: {msg}")
                time.sleep(wait)
        else:
            raise RuntimeError("upsertData failed after 6 retries")

        self.total_flushed += n
        self.reset()
        if self.verbose:
            print(f"    flushed {n:,} ops (total {self.total_flushed:,})")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-txns", type=int, default=0)
    args = parser.parse_args()

    data_dir = Path(__file__).parent.parent / "data" / "HHGOA_IEEE"
    host = os.getenv("TIGERGRAPH_HOST", "")
    user = os.getenv("TIGERGRAPH_USER", "")
    secret = os.getenv("TIGERGRAPH_SECRET", "")
    token = os.getenv("TIGERGRAPH_TOKEN", "")
    graph = os.getenv("TIGERGRAPH_GRAPH", "FraudInvestigation")

    if not host:
        print("ERROR: Set TIGERGRAPH_HOST in .env")
        sys.exit(1)
    if not (user and secret) and not token:
        print("ERROR: Set TIGERGRAPH_USER + TIGERGRAPH_SECRET in .env")
        sys.exit(1)

    print(f"Host: {host}")
    print(f"Graph: {graph}")
    print(f"Data: {data_dir}")

    import pyTigerGraph as tg
    if user and secret:
        print(f"Auth: username/password as '{user}'")
        conn = tg.TigerGraphConnection(
            host=host, graphname=graph, username=user, password=secret
        )
    else:
        print(f"Auth: apiToken {token[:8]}...{token[-4:]}")
        conn = tg.TigerGraphConnection(host=host, graphname=graph, apiToken=token)

    print(f"Connected: {conn.echo()}")
    up = BatchUploader(conn, batch_size=2000)

    # ── Step 1: Identity + devices ──
    print()
    print("=" * 60)
    print("STEP 1: Identity records + device profiles")
    print("=" * 60)
    identity_path = data_dir / "identity.csv"
    identity_by_txn = {}
    devices_seen = set()
    count = 0

    if identity_path.exists():
        t0 = time.time()
        with open(identity_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                txn_id = row.get("TransactionID", "")
                if not txn_id:
                    continue
                device_id = derive_device_id(
                    row.get("DeviceInfo", ""), row.get("id_30", ""),
                    row.get("id_31", ""), row.get("id_33", ""),
                )
                identity_by_txn[txn_id] = device_id
                if device_id not in devices_seen:
                    devices_seen.add(device_id)
                    up.vertex("DeviceProfile", device_id)
                count += 1
                if count % 20000 == 0:
                    print(f"  {count:,} identity records "
                          f"({len(devices_seen):,} devices)")
        up.flush()
        print(f"  Done: {count:,} identity, {len(devices_seen):,} devices "
              f"({time.time() - t0:.1f}s)")
    else:
        print("  SKIP: not found")

    # ── Step 2: Closed cases ──
    print()
    print("=" * 60)
    print("STEP 2: Closed cases")
    print("=" * 60)
    cases_path = data_dir / "closed_cases_history.csv"
    case_rows = []
    if cases_path.exists():
        t0 = time.time()
        with open(cases_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                case_id = row.get("case_id", "")
                if not case_id:
                    continue
                case_rows.append(row)
                up.vertex("ClosedCase", case_id)
        up.flush()
        print(f"  Done: {len(case_rows):,} closed cases ({time.time() - t0:.1f}s)")
    else:
        print("  SKIP: not found")

    # ── Step 3: Transactions ──
    print()
    print("=" * 60)
    print("STEP 3: Transactions")
    print("=" * 60)
    txn_path = data_dir / "transactions.csv"
    if not txn_path.exists():
        print(f"  ERROR: {txn_path} not found")
        sys.exit(1)

    card_map = {}
    customer_cards = defaultdict(set)
    customers_seen = set()
    regions_seen = set()
    domains_seen = set()
    txn_count = 0
    t0 = time.time()

    with open(txn_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            txn_id = row.get("TransactionID", "")
            customer_id = row.get("customer_id", "")
            if not txn_id or not customer_id:
                continue

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
                if customer_id not in customers_seen:
                    customers_seen.add(customer_id)
                    up.vertex("Customer", customer_id)
                up.vertex("Card", card_id)
                up.edge("Customer", customer_id, "OWNS", "Card", card_id)

            card_id = card_map[composite]
            up.vertex("Transaction", txn_id)
            up.edge("Card", card_id, "MADE", "Transaction", txn_id)

            addr1 = safe_float(row.get("addr1", "0"))
            if addr1:
                region_id = str(int(addr1))
                if region_id not in regions_seen:
                    regions_seen.add(region_id)
                    up.vertex("BillingRegion", region_id)
                up.edge("Transaction", txn_id, "BILLED_IN", "BillingRegion", region_id)

            p_email = row.get("P_emaildomain", "")
            if p_email:
                if p_email not in domains_seen:
                    domains_seen.add(p_email)
                    up.vertex("EmailDomain", p_email)
                up.edge("Transaction", txn_id, "PURCHASER_EMAIL", "EmailDomain", p_email)

            r_email = row.get("R_emaildomain", "")
            if r_email:
                if r_email not in domains_seen:
                    domains_seen.add(r_email)
                    up.vertex("EmailDomain", r_email)
                up.edge("Transaction", txn_id, "RECIPIENT_EMAIL", "EmailDomain", r_email)

            device_id = identity_by_txn.get(txn_id)
            if device_id:
                up.edge("Transaction", txn_id, "FROM_DEVICE", "DeviceProfile", device_id)

            txn_count += 1
            if txn_count % 2000 == 0:
                up.flush()
                elapsed = time.time() - t0
                rate = txn_count / elapsed if elapsed > 0 else 0
                print(f"  {txn_count:,} txns ({rate:.0f}/s, "
                      f"{len(card_map):,} cards)")

            if args.max_txns and txn_count >= args.max_txns:
                print(f"  Reached limit: {args.max_txns}")
                break

    up.flush()
    print(f"  Done: {txn_count:,} txns, {len(card_map):,} cards "
          f"({time.time() - t0:.1f}s)")

    # ── Step 4: Case -> card edges ──
    if case_rows:
        print()
        print("=" * 60)
        print("STEP 4: Closed case -> card links")
        print("=" * 60)
        loaded_cards = set(card_map.values())
        linked = 0
        for row in case_rows:
            case_id = row.get("case_id", "")
            card_id = row.get("card_id", "")
            if case_id and card_id in loaded_cards:
                up.edge("ClosedCase", case_id, "ON_CARD", "Card", card_id)
                linked += 1
        up.flush()
        print(f"  Done: {linked:,} links")

    print()
    print("=" * 60)
    print("LOAD COMPLETE")
    print("=" * 60)
    print(f"  Customers:       {len(customers_seen):>8,}")
    print(f"  Cards:           {len(card_map):>8,}")
    print(f"  Transactions:    {txn_count:>8,}")
    print(f"  Device profiles: {len(devices_seen):>8,}")
    print(f"  Email domains:   {len(domains_seen):>8,}")
    print(f"  Billing regions: {len(regions_seen):>8,}")
    print(f"  Closed cases:    {len(case_rows):>8,}")
    print(f"  Network retries: {up.retries}")
    print()
    print("Verify: USE GRAPH FraudInvestigation; SELECT count(*) FROM Customer")


if __name__ == "__main__":
    main()