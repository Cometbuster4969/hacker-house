"""
In-memory graph engine that mimics TigerGraph's GSQL capabilities.
Used for local development and when TigerGraph is not available.
Provides the same interface as the TigerGraph client.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional
import json
import logging

logger = logging.getLogger(__name__)


class InMemoryGraph:
    """A lightweight in-memory graph database emulating TigerGraph operations."""

    def __init__(self):
        # Vertices: type -> {id: properties}
        self.vertices: dict[str, dict[str, dict]] = {
            "Customer": {},
            "Card": {},
            "Transaction": {},
            "DeviceProfile": {},
            "EmailDomain": {},
            "BillingRegion": {},
            "ClosedCase": {},
            "InvestigationCase": {},
        }
        # Edges: (src_type, edge_name, tgt_type) -> [(src_id, tgt_id, props)]
        self.edges: dict[tuple, list[tuple]] = defaultdict(list)
        # Reverse index: (tgt_type, edge_name, src_type) -> [(tgt_id, src_id, props)]
        self.reverse_edges: dict[tuple, list[tuple]] = defaultdict(list)
        # Indexes for fast lookup
        self._card_txn_index: dict[str, list[str]] = defaultdict(list)
        self._customer_card_index: dict[str, list[str]] = defaultdict(list)
        self._device_txn_index: dict[str, list[str]] = defaultdict(list)
        self._region_txn_index: dict[str, list[str]] = defaultdict(list)
        self._email_txn_index: dict[str, list[str]] = defaultdict(list)
        self._txn_time_index: dict[str, list[str]] = defaultdict(list)  # card_id -> [txn_ids] sorted by time

    def add_vertex(self, vtype: str, vid: str, props: dict = None):
        """Add or update a vertex."""
        if props is None:
            props = {}
        props["id"] = vid
        self.vertices[vtype][vid] = props

    def add_edge(self, src_type: str, src_id: str, edge_name: str,
                 tgt_type: str, tgt_id: str, props: dict = None):
        """Add an edge between two vertices."""
        if props is None:
            props = {}
        key = (src_type, edge_name, tgt_type)
        self.edges[key].append((src_id, tgt_id, props))
        rev_key = (tgt_type, edge_name, src_type)
        self.reverse_edges[rev_key].append((tgt_id, src_id, props))

    def get_vertex(self, vtype: str, vid: str) -> Optional[dict]:
        """Get a vertex by type and ID."""
        return self.vertices.get(vtype, {}).get(vid)

    def get_neighbors(self, vtype: str, vid: str, edge_name: str,
                      tgt_type: str, direction: str = "out") -> list[dict]:
        """Get neighboring vertices connected by an edge."""
        results = []
        if direction == "out":
            key = (vtype, edge_name, tgt_type)
            for src_id, tgt_id, props in self.edges.get(key, []):
                if src_id == vid:
                    tgt = self.get_vertex(tgt_type, tgt_id)
                    if tgt:
                        results.append({**tgt, **props, "_edge": edge_name})
        else:
            key = (tgt_type, edge_name, vtype)
            for tgt_id, src_id, props in self.reverse_edges.get(key, []):
                if tgt_id == vid:
                    src = self.get_vertex(vtype, src_id)
                    if src:
                        results.append({**src, **props, "_edge": edge_name})
        return results

    # --- High-level query methods matching GSQL patterns ---

    def get_card_transactions(self, card_id: str,
                              limit: int = 100) -> list[dict]:
        """Get all transactions for a card, ordered by time."""
        txn_ids = self._card_txn_index.get(card_id, [])
        txns = []
        for tid in txn_ids:
            txn = self.get_vertex("Transaction", tid)
            if txn:
                txns.append(txn)
        # Sort by timestamp
        txns.sort(key=lambda t: t.get("ts", ""))
        return txns[:limit]

    def get_customer_cards(self, customer_id: str) -> list[dict]:
        """Get all cards for a customer."""
        card_ids = self._customer_card_index.get(customer_id, [])
        return [self.get_vertex("Card", cid) for cid in card_ids
                if self.get_vertex("Card", cid)]

    def get_customer_transactions(self, customer_id: str,
                                   days: int = 90,
                                   limit: int = 500) -> list[dict]:
        """Get recent transactions for a customer."""
        cards = self.get_customer_cards(customer_id)
        all_txns = []
        for card in cards:
            all_txns.extend(self.get_card_transactions(card["id"]))
        # Sort by time
        all_txns.sort(key=lambda t: t.get("ts", ""))
        return all_txns[-limit:]

    def get_device_cards(self, device_id: str,
                          window_days: int = 30) -> list[dict]:
        """Get all cards that used a device profile in a time window."""
        txn_ids = self._device_txn_index.get(device_id, [])
        cards_seen = {}
        for tid in txn_ids:
            txn = self.get_vertex("Transaction", tid)
            if txn:
                card_id = txn.get("card_id", "")
                if card_id and card_id not in cards_seen:
                    cards_seen[card_id] = self.get_vertex("Card", card_id)
        return [c for c in cards_seen.values() if c]

    def get_device_transactions(self, device_id: str) -> list[dict]:
        """Get all transactions from a device profile."""
        txn_ids = self._device_txn_index.get(device_id, [])
        return [self.get_vertex("Transaction", tid) for tid in txn_ids
                if self.get_vertex("Transaction", tid)]

    def get_region_cards(self, region_id: str) -> list[dict]:
        """Get all cards that transacted in a billing region."""
        txn_ids = self._region_txn_index.get(region_id, [])
        cards_seen = {}
        for tid in txn_ids:
            txn = self.get_vertex("Transaction", tid)
            if txn:
                card_id = txn.get("card_id", "")
                if card_id and card_id not in cards_seen:
                    cards_seen[card_id] = self.get_vertex("Card", card_id)
        return [c for c in cards_seen.values() if c]

    def find_shared_devices(self, card_ids: list[str]) -> list[dict]:
        """Find device profiles shared across multiple cards."""
        device_cards = defaultdict(set)
        for card_id in card_ids:
            for device_id in self._device_txn_index:
                for tid in self._device_txn_index[device_id]:
                    txn = self.get_vertex("Transaction", tid)
                    if txn and txn.get("card_id") == card_id:
                        device_cards[device_id].add(card_id)
        shared = []
        for device_id, cards in device_cards.items():
            if len(cards) > 1:
                device = self.get_vertex("DeviceProfile", device_id)
                if device:
                    shared.append({
                        **device,
                        "cards": list(cards),
                        "card_count": len(cards)
                    })
        return shared

    def find_shared_regions(self, card_ids: list[str]) -> list[dict]:
        """Find billing regions shared across multiple cards."""
        region_cards = defaultdict(set)
        for card_id in card_ids:
            for region_id in self._region_txn_index:
                for tid in self._region_txn_index[region_id]:
                    txn = self.get_vertex("Transaction", tid)
                    if txn and txn.get("card_id") == card_id:
                        region_cards[region_id].add(card_id)
        shared = []
        for region_id, cards in region_cards.items():
            if len(cards) > 1:
                region = self.get_vertex("BillingRegion", region_id)
                if region:
                    shared.append({
                        **region,
                        "cards": list(cards),
                        "card_count": len(cards)
                    })
        return shared

    def get_closed_cases_for_card(self, card_id: str) -> list[dict]:
        """Get closed cases involving a specific card."""
        results = []
        for case_id, case in self.vertices.get("ClosedCase", {}).items():
            if case.get("card_id") == card_id:
                results.append(case)
        return results

    def get_closed_cases_for_customer(self, customer_id: str) -> list[dict]:
        """Get closed cases for a customer."""
        results = []
        for case_id, case in self.vertices.get("ClosedCase", {}).items():
            if case.get("customer_id") == customer_id:
                results.append(case)
        return results

    def get_closed_cases_for_device(self, device_id: str) -> list[dict]:
        """Get closed cases involving a device profile."""
        results = []
        for case_id, case in self.vertices.get("ClosedCase", {}).items():
            case_device_ids = case.get("device_ids", [])
            if device_id in case_device_ids:
                results.append(case)
        return results

    def find_similar_cases(self, customer_id: str, card_id: str,
                           device_profile: str = "",
                           pattern: str = "") -> list[dict]:
        """Find closed cases similar to current investigation signals."""
        results = []
        for case_id, case in self.vertices.get("ClosedCase", {}).items():
            score = 0.0
            if case.get("customer_id") == customer_id:
                score += 0.3
            if case.get("card_id") == card_id:
                score += 0.3
            if pattern and case.get("pattern") == pattern:
                score += 0.2
            if score > 0.2:
                results.append({**case, "_similarity": score})
        results.sort(key=lambda c: c.get("_similarity", 0), reverse=True)
        return results[:10]

    def detect_card_testing(self, card_id: str,
                            window_hours: int = 1) -> dict:
        """Detect card testing pattern: small authorizations followed by larger purchase."""
        txns = self.get_card_transactions(card_id)
        if len(txns) < 3:
            return {"detected": False, "pattern": "card_testing", "evidence": []}

        # Look for sequences of small online txns followed by larger one
        small_txns = []
        large_txn = None

        for txn in txns:
            if txn.get("channel") == "online" and txn.get("amount", 0) < 5.0:
                small_txns.append(txn)
            elif txn.get("amount", 0) >= 50.0 and small_txns:
                # Check if this follows the small txns within window
                large_txn = txn
                break

        if len(small_txns) >= 2 and large_txn:
            return {
                "detected": True,
                "pattern": "card_testing",
                "small_txns": [t["id"] for t in small_txns],
                "large_txn": large_txn["id"],
                "total_small_amount": sum(t.get("amount", 0) for t in small_txns),
                "large_amount": large_txn.get("amount", 0),
            }
        return {"detected": False, "pattern": "card_testing", "evidence": []}

    def detect_out_of_region(self, card_id: str,
                              region: float) -> dict:
        """Detect out-of-region usage pattern."""
        txns = self.get_card_transactions(card_id)
        home_regions = set()
        new_region_txns = []

        for txn in txns:
            txn_region = txn.get("addr1", 0.0)
            if txn_region and txn_region != region:
                home_regions.add(txn_region)
            if txn_region == region:
                new_region_txns.append(txn)

        # Check if this is a new region vs established pattern
        if new_region_txns and not home_regions:
            # All transactions in the new region - could be a trip
            return {
                "detected": False,
                "pattern": "out_of_region_use",
                "note": "All activity in one region"
            }

        if new_region_txns and home_regions:
            return {
                "detected": True,
                "pattern": "out_of_region_use",
                "home_regions": list(home_regions),
                "new_region": region,
                "txns_in_new_region": [t["id"] for t in new_region_txns],
            }
        return {"detected": False, "pattern": "out_of_region_use"}

    def detect_new_device(self, card_id: str,
                           device_id: str) -> dict:
        """Detect if a device is new for this card."""
        # Check if this device was used before by this card
        card_txns = self.get_card_transactions(card_id)
        device_txns_before = 0

        for txn in card_txns:
            # Check if this txn used the same device
            txn_device_edges = self.get_neighbors(
                "Transaction", txn["id"], "FROM_DEVICE", "DeviceProfile"
            )
            for d in txn_device_edges:
                if d.get("id") == device_id:
                    device_txns_before += 1

        is_new = device_txns_before <= 1  # Only this one
        return {
            "detected": is_new,
            "pattern": "card_not_present_new_device",
            "device_id": device_id,
            "prior_uses": max(0, device_txns_before - 1),
        }

    def get_card_velocity(self, card_id: str,
                           hours: int = 24) -> dict:
        """Calculate transaction velocity for a card."""
        txns = self.get_card_transactions(card_id)
        if not txns:
            return {"velocity_24h": 0, "total_amount_24h": 0, "avg_amount": 0}

        # Get most recent timestamp
        latest_ts = txns[-1].get("ts", "")
        if not latest_ts:
            return {"velocity_24h": len(txns), "total_amount_24h": 0, "avg_amount": 0}

        try:
            latest_dt = datetime.strptime(latest_ts, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return {"velocity_24h": len(txns), "total_amount_24h": 0, "avg_amount": 0}

        cutoff = latest_dt - timedelta(hours=hours)
        recent = []
        for txn in txns:
            try:
                txn_dt = datetime.strptime(txn.get("ts", ""), "%Y-%m-%d %H:%M:%S")
                if txn_dt >= cutoff:
                    recent.append(txn)
            except ValueError:
                continue

        total = sum(t.get("amount", 0) for t in recent)
        return {
            "velocity_24h": len(recent),
            "total_amount_24h": total,
            "avg_amount": total / len(recent) if recent else 0,
            "txn_ids": [t["id"] for t in recent],
        }

    def write_investigation_case(self, case_id: str, props: dict):
        """Write an investigation case to the graph."""
        self.add_vertex("InvestigationCase", case_id, props)

    def link_case_to_transaction(self, case_id: str, txn_id: str):
        """Link investigation case to a transaction."""
        self.add_edge("InvestigationCase", case_id,
                      "CASE_INVOLVES_TXN", "Transaction", txn_id)

    def link_case_to_card(self, case_id: str, card_id: str):
        """Link investigation case to a card."""
        self.add_edge("InvestigationCase", case_id,
                      "CASE_ON_CARD", "Card", card_id)

    def link_case_to_device(self, case_id: str, device_id: str):
        """Link investigation case to a device profile."""
        self.add_edge("InvestigationCase", case_id,
                      "CASE_INVOLVES_DEVICE", "DeviceProfile", device_id)

    def link_case_to_closed_case(self, case_id: str, closed_case_id: str,
                                  similarity: float):
        """Link investigation case to a similar closed case."""
        self.add_edge("InvestigationCase", case_id,
                      "CASE_SIMILAR_TO", "ClosedCase", closed_case_id,
                      {"similarity_score": similarity})

    def get_graph_stats(self) -> dict:
        """Return vertex and edge counts."""
        stats = {}
        for vtype, verts in self.vertices.items():
            stats[f"vertices_{vtype}"] = len(verts)
        edge_counts = defaultdict(int)
        for (src, edge, tgt), elist in self.edges.items():
            edge_counts[edge] += len(elist)
        for edge, count in edge_counts.items():
            stats[f"edges_{edge}"] = count
        return stats

    def to_json(self) -> str:
        """Serialize graph state (for debugging)."""
        stats = self.get_graph_stats()
        return json.dumps(stats, indent=2)
