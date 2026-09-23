"""
Data loader: reads CSV files and populates the in-memory graph.
Handles the HHGOA_IEEE dataset structure.
"""
from __future__ import annotations
import csv
import hashlib
import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import Optional

from .in_memory_graph import InMemoryGraph
from ..utils.models import Transaction, IdentityRecord, ClosedCase, CasePackEntry

logger = logging.getLogger(__name__)


def derive_card_id(customer_id: str, card_columns: list[str]) -> str:
    """
    Derive a deterministic card_id from customer_id + card2..card6 columns.
    Uses sorted combinations to ensure consistency.
    """
    # Create a stable hash from card columns
    key = "|".join(str(c) for c in card_columns)
    hash_suffix = hashlib.md5(key.encode()).hexdigest()[:4].upper()
    # Use a sequential index approach based on the hash
    return f"{customer_id}-K{int(hash_suffix, 16) % 10 + 1}"


def derive_device_id(device_info: str, id_30: str, id_31: str, id_33: str) -> str:
    """Derive a deterministic device_id from identity columns."""
    key = f"{device_info}|{id_30}|{id_31}|{id_33}"
    hash_val = hashlib.md5(key.encode()).hexdigest()[:6].upper()
    return f"D{hash_val}"


class DataLoader:
    """Loads HHGOA_IEEE dataset into the graph."""

    def __init__(self, data_dir: str, graph: InMemoryGraph):
        self.data_dir = Path(data_dir)
        self.graph = graph
        self._identity_by_txn: dict[str, IdentityRecord] = {}
        self._card_map: dict[str, str] = {}  # composite_key -> card_id
        self._customer_cards: dict[str, set] = defaultdict(set)
        self._txn_counter = 0

    def load_all(self, max_transactions: int = 0):
        """Load all data files."""
        logger.info("Loading dataset from %s", self.data_dir)

        # Load identity first (smaller file)
        self._load_identity()

        # Load closed cases
        self._load_closed_cases()

        # Load case pack
        self._load_case_pack()

        # Load transactions (largest file)
        self._load_transactions(max_transactions)

        # Build indexes
        self._build_indexes()

        stats = self.graph.get_graph_stats()
        logger.info("Graph loaded: %s", stats)

    def _load_identity(self):
        """Load identity.csv into memory and graph."""
        path = self.data_dir / "identity.csv"
        if not path.exists():
            logger.warning("identity.csv not found at %s", path)
            return

        count = 0
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                txn_id = row.get("TransactionID", "")
                if not txn_id:
                    continue

                identity = IdentityRecord(
                    transaction_id=txn_id,
                    device_type=row.get("DeviceType", ""),
                    device_info=row.get("DeviceInfo", ""),
                    id_01=_safe_float(row.get("id_01", "0")),
                    id_02=_safe_float(row.get("id_02", "0")),
                    id_03=_safe_float(row.get("id_03", "0")),
                    id_04=_safe_float(row.get("id_04", "0")),
                    id_05=_safe_float(row.get("id_05", "0")),
                    id_06=_safe_float(row.get("id_06", "0")),
                    id_07=_safe_float(row.get("id_07", "0")),
                    id_08=_safe_float(row.get("id_08", "0")),
                    id_09=_safe_float(row.get("id_09", "0")),
                    id_10=_safe_float(row.get("id_10", "0")),
                    id_11=_safe_float(row.get("id_11", "0")),
                    id_12=row.get("id_12", ""),
                    id_13=row.get("id_13", ""),
                    id_14=row.get("id_14", ""),
                    id_15=row.get("id_15", ""),  # New / Found
                    id_16=row.get("id_16", ""),
                    id_17=row.get("id_17", ""),
                    id_18=row.get("id_18", ""),
                    id_19=row.get("id_19", ""),
                    id_20=row.get("id_20", ""),
                    id_21=row.get("id_21", ""),
                    id_22=row.get("id_22", ""),
                    id_23=row.get("id_23", ""),  # proxy
                    id_24=row.get("id_24", ""),
                    id_25=row.get("id_25", ""),
                    id_26=row.get("id_26", ""),
                    id_27=row.get("id_27", ""),
                    id_28=row.get("id_28", ""),
                    id_29=row.get("id_29", ""),
                    id_30=row.get("id_30", ""),  # OS
                    id_31=row.get("id_31", ""),  # browser
                    id_32=row.get("id_32", ""),
                    id_33=row.get("id_33", ""),  # screen
                    id_34=row.get("id_34", ""),  # match
                    id_35=row.get("id_35", ""),
                    id_36=row.get("id_36", ""),
                    id_37=row.get("id_37", ""),
                    id_38=row.get("id_38", ""),
                    raw_data=dict(row),
                )
                self._identity_by_txn[txn_id] = identity

                # Create DeviceProfile vertex
                device_id = derive_device_id(
                    identity.device_info,
                    identity.id_30,
                    identity.id_31,
                    identity.id_33,
                )
                self.graph.add_vertex("DeviceProfile", device_id, {
                    "device_id": device_id,
                    "device_info": identity.device_info,
                    "device_type": identity.device_type,
                    "os": identity.id_30,
                    "browser": identity.id_31,
                    "screen": identity.id_33,
                })

                count += 1
                if count % 50000 == 0:
                    logger.info("Loaded %d identity records", count)

        logger.info("Loaded %d identity records total", count)

    def _load_closed_cases(self):
        """Load closed_cases_history.csv."""
        path = self.data_dir / "closed_cases_history.csv"
        if not path.exists():
            logger.warning("closed_cases_history.csv not found at %s", path)
            return

        count = 0
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                case_id = row.get("case_id", "")
                if not case_id:
                    continue

                txn_ids = [t.strip() for t in row.get("txn_ids", "").split("|")
                           if t.strip()]
                connected_cards = [c.strip() for c in
                                   row.get("connected_card_ids", "").split("|")
                                   if c.strip()]

                case = ClosedCase(
                    case_id=case_id,
                    customer_id=row.get("customer_id", ""),
                    card_id=row.get("card_id", ""),
                    opened_at=row.get("opened_at", ""),
                    closed_at=row.get("closed_at", ""),
                    outcome=row.get("outcome", ""),
                    pattern=row.get("pattern", "none"),
                    first_fraud_txn_id=row.get("first_fraud_txn_id", ""),
                    txn_ids=txn_ids,
                    n_txns=int(row.get("n_txns", "0") or "0"),
                    exposure_usd=_safe_float(row.get("exposure_usd", "0")),
                    connected_card_ids=connected_cards,
                    actions_taken=row.get("actions_taken", ""),
                    report_filed=row.get("report_filed", "").lower() in ("true", "1", "yes"),
                    analyst_notes=row.get("analyst_notes", ""),
                )

                self.graph.add_vertex("ClosedCase", case_id, case.model_dump())
                count += 1

        logger.info("Loaded %d closed cases", count)

    def _load_case_pack(self):
        """Load case_pack.csv (the 20 benchmark cases)."""
        path = self.data_dir / "case_pack.csv"
        if not path.exists():
            logger.warning("case_pack.csv not found at %s", path)
            return

        self.case_pack: list[CasePackEntry] = []
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                entry = CasePackEntry(
                    case_id=row.get("case_id", ""),
                    opened_at=row.get("opened_at", ""),
                    trigger_type=row.get("trigger_type", "risk_score"),
                    trigger_text=row.get("trigger_text", ""),
                    flagged_txn_id=row.get("flagged_txn_id", ""),
                    card_id=row.get("card_id", ""),
                    customer_id=row.get("customer_id", ""),
                    risk_score=_safe_float(row.get("risk_score")) if row.get("risk_score") else None,
                )
                self.case_pack.append(entry)

        logger.info("Loaded %d case pack entries", len(self.case_pack))

    def _load_transactions(self, max_rows: int = 0):
        """Load transactions.csv (the large file)."""
        path = self.data_dir / "transactions.csv"
        if not path.exists():
            logger.warning("transactions.csv not found at %s", path)
            return

        count = 0
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                txn_id = row.get("TransactionID", "")
                customer_id = row.get("customer_id", "")
                if not txn_id or not customer_id:
                    continue

                # Derive card_id from card columns
                card_key = "|".join([
                    row.get("card2", ""),
                    row.get("card3", ""),
                    row.get("card4", ""),
                    row.get("card5", ""),
                    row.get("card6", ""),
                ])
                composite = f"{customer_id}|{card_key}"

                if composite not in self._card_map:
                    idx = len(self._customer_cards[customer_id]) + 1
                    card_id = f"{customer_id}-K{idx}"
                    self._card_map[composite] = card_id
                    self._customer_cards[customer_id].add(card_id)

                    # Create Card vertex
                    self.graph.add_vertex("Card", card_id, {
                        "card_id": card_id,
                        "customer_id": customer_id,
                        "network": row.get("card4", ""),
                        "card_type": row.get("card6", ""),
                        "card1": row.get("card1", ""),
                    })

                    # Create Customer -> Card edge
                    self.graph.add_vertex("Customer", customer_id, {
                        "customer_id": customer_id,
                    })
                    self.graph.add_edge("Customer", customer_id, "OWNS",
                                        "Card", card_id)

                card_id = self._card_map[composite]

                amount = _safe_float(row.get("TransactionAmt", "0"))
                risk_score = _safe_float(row.get("risk_score", "0"))
                addr1 = _safe_float(row.get("addr1", "0"))

                # Create Transaction vertex
                txn_data = {
                    "id": txn_id,
                    "transaction_id": txn_id,
                    "customer_id": customer_id,
                    "card_id": card_id,
                    "amount": amount,
                    "ts": row.get("ts", ""),
                    "channel": row.get("channel", ""),
                    "risk_score": risk_score,
                    "product_cd": row.get("ProductCD", ""),
                    "addr1": addr1,
                    "addr2": _safe_float(row.get("addr2", "0")),
                    "p_emaildomain": row.get("P_emaildomain", ""),
                    "r_emaildomain": row.get("R_emaildomain", ""),
                }
                self.graph.add_vertex("Transaction", txn_id, txn_data)

                # Card -> Transaction edge
                self.graph.add_edge("Card", card_id, "MADE",
                                    "Transaction", txn_id)

                # Billing region
                region_id = str(int(addr1)) if addr1 else "UNKNOWN"
                if not self.graph.get_vertex("BillingRegion", region_id):
                    self.graph.add_vertex("BillingRegion", region_id, {
                        "region_id": region_id,
                        "country_code": _safe_float(row.get("addr2", "0")),
                    })
                self.graph.add_edge("Transaction", txn_id, "BILLED_IN",
                                    "BillingRegion", region_id)

                # Email domains
                p_email = row.get("P_emaildomain", "")
                if p_email:
                    if not self.graph.get_vertex("EmailDomain", p_email):
                        self.graph.add_vertex("EmailDomain", p_email, {
                            "domain": p_email,
                            "domain_type": "purchaser",
                        })
                    self.graph.add_edge("Transaction", txn_id, "PURCHASER_EMAIL",
                                        "EmailDomain", p_email)

                r_email = row.get("R_emaildomain", "")
                if r_email:
                    if not self.graph.get_vertex("EmailDomain", r_email):
                        self.graph.add_vertex("EmailDomain", r_email, {
                            "domain": r_email,
                            "domain_type": "recipient",
                        })
                    self.graph.add_edge("Transaction", txn_id, "RECIPIENT_EMAIL",
                                        "EmailDomain", r_email)

                # Device link (if identity exists)
                identity = self._identity_by_txn.get(txn_id)
                if identity:
                    device_id = derive_device_id(
                        identity.device_info,
                        identity.id_30,
                        identity.id_31,
                        identity.id_33,
                    )
                    self.graph.add_edge("Transaction", txn_id, "FROM_DEVICE",
                                        "DeviceProfile", device_id, {
                        "is_new_device": identity.id_15.lower() == "new",
                        "proxy_type": identity.id_23,
                    })

                    # Track device -> transaction for indexing
                    self.graph._device_txn_index[device_id].append(txn_id)

                # Track indexes
                self.graph._card_txn_index[card_id].append(txn_id)
                self.graph._region_txn_index[region_id].append(txn_id)
                if p_email:
                    self.graph._email_txn_index[p_email].append(txn_id)

                count += 1
                if max_rows and count >= max_rows:
                    logger.info("Reached max_rows limit: %d", max_rows)
                    break
                if count % 100000 == 0:
                    logger.info("Loaded %d transactions", count)

        logger.info("Loaded %d transactions total", count)

    def _build_indexes(self):
        """Build additional indexes after loading."""
        # Customer -> Card index
        for customer_id, card_ids in self._customer_cards.items():
            self.graph._customer_card_index[customer_id] = list(card_ids)

        # Sort transaction indexes by time
        for card_id in self.graph._card_txn_index:
            txn_ids = self.graph._card_txn_index[card_id]
            txn_ids.sort(key=lambda tid: self.graph.get_vertex("Transaction", tid).get("ts", "") if self.graph.get_vertex("Transaction", tid) else "")

        logger.info("Indexes built")

    def get_case_pack(self) -> list[CasePackEntry]:
        """Return loaded case pack entries."""
        return getattr(self, "case_pack", [])


def _safe_float(val) -> float:
    """Safely convert a value to float."""
    try:
        return float(val) if val else 0.0
    except (ValueError, TypeError):
        return 0.0
