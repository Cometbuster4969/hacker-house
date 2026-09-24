"""
Evidence Gatherer: collects structured evidence from the graph for a case.
Implements the core investigation queries that the agent uses.
"""
from __future__ import annotations
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from ..graph.in_memory_graph import InMemoryGraph
from ..utils.models import (
    Transaction, IdentityRecord, ClosedCase, Evidence, SourceType,
    InvestigationState, CasePackEntry, FraudPattern,
)

logger = logging.getLogger(__name__)


class EvidenceGatherer:
    """Gathers evidence from the graph for an investigation."""

    def __init__(self, graph: InMemoryGraph):
        self.graph = graph

    def _txn_from_graph(self, txn: dict) -> Transaction:
        """Convert a graph vertex dict to a Transaction model."""
        return Transaction(
            transaction_id=txn.get("transaction_id", txn.get("id", "")),
            customer_id=txn.get("customer_id", ""),
            card_id=txn.get("card_id", ""),
            amount=txn.get("amount", 0.0),
            timestamp=txn.get("ts", ""),
            channel=txn.get("channel", ""),
            risk_score=txn.get("risk_score", 0.0),
            product_cd=txn.get("product_cd", ""),
            addr1=txn.get("addr1", 0.0),
            addr2=txn.get("addr2", 0.0),
            p_emaildomain=txn.get("p_emaildomain", ""),
            r_emaildomain=txn.get("r_emaildomain", ""),
        )

    def gather_initial_evidence(self, state: InvestigationState) -> InvestigationState:
        """Step 1: Gather evidence from the flagged transaction and its context."""
        trigger = state.trigger
        txn_id = trigger.flagged_txn_id
        card_id = trigger.card_id
        customer_id = trigger.customer_id

        # 1. Get the flagged transaction
        txn = self.graph.get_vertex("Transaction", txn_id)
        if txn:
            state.flagged_txn = self._txn_from_graph(txn)

            state.evidence_collected.append(Evidence(
                claim=f"Flagged transaction {txn_id}: ${txn.get('amount', 0):.2f}, "
                      f"{txn.get('channel', 'unknown')} channel, "
                      f"risk score {txn.get('risk_score', 0):.2f}",
                source=SourceType.GRAPH,
                ref=f"query:get_transaction({txn_id})",
                entity_ids=[txn_id],
            ))
            state.tool_calls += 1

        # 2. Get identity record for the transaction
        identity_data = None
        for device_id, txn_ids in self.graph._device_txn_index.items():
            if txn_id in txn_ids:
                device = self.graph.get_vertex("DeviceProfile", device_id)
                if device:
                    identity_data = device
                    state.evidence_collected.append(Evidence(
                        claim=f"Transaction came from device: {device.get('device_info', 'unknown')} "
                              f"({device.get('os', '?')}, {device.get('browser', '?')}, "
                              f"{device.get('screen', '?')})",
                        source=SourceType.GRAPH,
                        ref=f"query:get_device({device_id})",
                        entity_ids=[device_id],
                    ))
                    state.tool_calls += 1
                    break

        # 3. Get card transaction history (last 30 txns)
        card_txns = self.graph.get_card_transactions(card_id, limit=30)
        state.card_transactions = [self._txn_from_graph(t) for t in card_txns]
        state.tool_calls += 1

        if card_txns:
            avg_amount = sum(t.get("amount", 0) for t in card_txns) / len(card_txns)
            state.evidence_collected.append(Evidence(
                claim=f"Card {card_id} has {len(card_txns)} transactions in history, "
                      f"average amount ${avg_amount:.2f}",
                source=SourceType.GRAPH,
                ref=f"query:card_transactions({card_id})",
                entity_ids=[card_id],
            ))

        # 4. Get customer's other cards
        customer_cards = self.graph.get_customer_cards(customer_id)
        state.tool_calls += 1

        if len(customer_cards) > 1:
            other_cards = [c["id"] for c in customer_cards if c["id"] != card_id]
            state.evidence_collected.append(Evidence(
                claim=f"Customer {customer_id} has {len(customer_cards)} cards: "
                      f"{', '.join(c['id'] for c in customer_cards)}",
                source=SourceType.GRAPH,
                ref=f"query:customer_cards({customer_id})",
                entity_ids=[customer_id] + [c["id"] for c in customer_cards],
            ))

        # 5. Get billing region context
        if txn and txn.get("addr1"):
            region_id = str(int(txn["addr1"]))
            region_cards = self.graph.get_region_cards(region_id)
            state.tool_calls += 1
            if len(region_cards) > 1:
                state.evidence_collected.append(Evidence(
                    claim=f"Billing region {region_id} has {len(region_cards)} cards transacting in it",
                    source=SourceType.GRAPH,
                    ref=f"query:region_cards({region_id})",
                    entity_ids=[region_id],
                ))

        # 6. Check closed cases for this card/customer
        card_cases = self.graph.get_closed_cases_for_card(card_id)
        customer_cases = self.graph.get_closed_cases_for_customer(customer_id)
        state.tool_calls += 2

        all_cases = {c.get("case_id", c.get("id", "")): c for c in card_cases + customer_cases}
        state.similar_closed_cases = [
            ClosedCase(
                case_id=c.get("case_id", ""),
                customer_id=c.get("customer_id", ""),
                card_id=c.get("card_id", ""),
                opened_at=c.get("opened_at", ""),
                closed_at=c.get("closed_at", ""),
                outcome=c.get("outcome", ""),
                pattern=c.get("pattern", "none"),
                first_fraud_txn_id=c.get("first_fraud_txn_id", ""),
                exposure_usd=float(c.get("exposure_usd", 0)),
                n_txns=int(c.get("n_txns", 0)),
                analyst_notes=c.get("analyst_notes", ""),
            )
            for c in all_cases.values()
        ]

        if state.similar_closed_cases:
            case_ids = [c.case_id for c in state.similar_closed_cases]
            state.evidence_collected.append(Evidence(
                claim=f"Found {len(state.similar_closed_cases)} prior closed cases "
                      f"for this card/customer: {', '.join(case_ids)}",
                source=SourceType.GRAPH,
                ref=f"query:closed_cases({card_id}, {customer_id})",
                entity_ids=case_ids,
            ))

        return state

    def gather_device_evidence(self, state: InvestigationState) -> InvestigationState:
        """Step 2: Investigate device profile connections."""
        flagged_txn_id = state.trigger.flagged_txn_id

        # Find device for flagged transaction
        device_id = None
        for did, txn_ids in self.graph._device_txn_index.items():
            if flagged_txn_id in txn_ids:
                device_id = did
                break

        if not device_id:
            return state

        # Get all cards on this device
        device_cards = self.graph.get_device_cards(device_id)
        state.tool_calls += 1

        if len(device_cards) > 1:
            card_ids = [c["id"] for c in device_cards]
            state.device_neighbors.append({
                "device_id": device_id,
                "cards": card_ids,
                "card_count": len(card_ids),
            })
            state.evidence_collected.append(Evidence(
                claim=f"Device {device_id} used by {len(device_cards)} cards: "
                      f"{', '.join(card_ids)}",
                source=SourceType.GRAPH,
                ref=f"query:device_cards({device_id})",
                entity_ids=[device_id] + card_ids,
            ))

            # Get transactions for other cards on this device
            for card in device_cards:
                if card["id"] != state.trigger.card_id:
                    txns = self.graph.get_card_transactions(card["id"], limit=5)
                    state.connected_transactions.extend([
                        self._txn_from_graph(t) for t in txns
                    ])

        # Check if device is "New" for this card
        new_device_check = self.graph.detect_new_device(
            state.trigger.card_id, device_id
        )
        state.tool_calls += 1
        if new_device_check.get("detected"):
            state.evidence_collected.append(Evidence(
                claim=f"Device {device_id} is marked as NEW for card {state.trigger.card_id}",
                source=SourceType.GRAPH,
                ref=f"query:new_device_check({state.trigger.card_id}, {device_id})",
                entity_ids=[device_id],
            ))

        # Check closed cases on this device
        device_cases = self.graph.get_closed_cases_for_device(device_id)
        state.tool_calls += 1
        if device_cases:
            state.evidence_collected.append(Evidence(
                claim=f"Device {device_id} appears in {len(device_cases)} closed cases",
                source=SourceType.GRAPH,
                ref=f"query:device_closed_cases({device_id})",
                entity_ids=[c.get("case_id", "") for c in device_cases],
            ))

        return state

    def gather_velocity_evidence(self, state: InvestigationState) -> InvestigationState:
        """Step 3: Analyze transaction velocity patterns."""
        card_id = state.trigger.card_id

        velocity = self.graph.get_card_velocity(card_id, hours=24)
        state.tool_calls += 1

        if velocity.get("velocity_24h", 0) >= 3:
            state.evidence_collected.append(Evidence(
                claim=f"Card {card_id} shows high velocity: {velocity['velocity_24h']} "
                      f"transactions in 24h totaling ${velocity['total_amount_24h']:.2f}",
                source=SourceType.GRAPH,
                ref=f"query:card_velocity({card_id}, 24h)",
                entity_ids=velocity.get("txn_ids", []),
            ))

        return state

    def gather_pattern_evidence(self, state: InvestigationState) -> InvestigationState:
        """Step 4: Check for known fraud patterns."""
        card_id = state.trigger.card_id

        # Card testing detection
        testing = self.graph.detect_card_testing(card_id)
        state.tool_calls += 1
        if testing.get("detected"):
            state.pattern = FraudPattern.CARD_TESTING
            state.evidence_collected.append(Evidence(
                claim=f"Card testing detected: {len(testing.get('small_txns', []))} "
                      f"small authorizations (${testing.get('total_small_amount', 0):.2f}) "
                      f"followed by ${testing.get('large_amount', 0):.2f} purchase",
                source=SourceType.GRAPH,
                ref=f"query:detect_card_testing({card_id})",
                entity_ids=testing.get("small_txns", []) + [testing.get("large_txn", "")],
            ))

        # Out-of-region detection
        if state.flagged_txn and state.flagged_txn.addr1:
            region_check = self.graph.detect_out_of_region(
                card_id, state.flagged_txn.addr1
            )
            state.tool_calls += 1
            if region_check.get("detected"):
                if state.pattern == FraudPattern.NONE:
                    state.pattern = FraudPattern.OUT_OF_REGION_USE
                state.evidence_collected.append(Evidence(
                    claim=f"Out-of-region use detected: billing region "
                          f"{region_check.get('new_region')} is new for this card. "
                          f"Home regions: {region_check.get('home_regions', [])}",
                    source=SourceType.GRAPH,
                    ref=f"query:detect_out_of_region({card_id})",
                    entity_ids=region_check.get("txns_in_new_region", []),
                ))

        return state

    def find_similar_closed_cases(self, state: InvestigationState) -> InvestigationState:
        """Step 5: Retrieve similar past cases for case memory."""
        # Already partially done in initial evidence, but do deeper search
        customer_id = state.trigger.customer_id
        card_id = state.trigger.card_id

        similar = self.graph.find_similar_cases(customer_id, card_id)
        state.tool_calls += 1

        for case in similar[:5]:
            case_id = case.get("case_id", "")
            if case_id and case_id not in [c.case_id for c in state.similar_closed_cases]:
                closed = ClosedCase(
                    case_id=case.get("case_id", ""),
                    customer_id=case.get("customer_id", ""),
                    card_id=case.get("card_id", ""),
                    opened_at=case.get("opened_at", ""),
                    closed_at=case.get("closed_at", ""),
                    outcome=case.get("outcome", ""),
                    pattern=case.get("pattern", "none"),
                    first_fraud_txn_id=case.get("first_fraud_txn_id", ""),
                    exposure_usd=float(case.get("exposure_usd", 0)),
                    n_txns=int(case.get("n_txns", 0)),
                    analyst_notes=case.get("analyst_notes", ""),
                )
                state.similar_closed_cases.append(closed)

        return state
