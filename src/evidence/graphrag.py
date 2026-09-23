"""
GraphRAG: Retrieval-Augmented Generation using the knowledge graph.
Combines graph traversal results with policy documents to ground
the LLM in factual evidence.
"""
from __future__ import annotations
import logging
from typing import Optional

from ..graph.in_memory_graph import InMemoryGraph
from ..utils.models import InvestigationState, ClosedCase

logger = logging.getLogger(__name__)


class GraphRAGContext:
    """Synthesizes context from graph evidence and policy documents for LLM reasoning."""

    # Fraud policy rules (from the dataset)
    POLICY_RULES = {
        "R1": "Verify before you block on a weak signal. If the case rests on a single "
              "signal and fraud probability is below 0.70, recommend VERIFY_WITH_CUSTOMER "
              "or STEP_UP_AUTH before any block.",
        "R2": "Customer denies the transaction. Recommend BLOCK_CARD and CREATE_CASE. "
              "Add FILE_REPORT if exposure exceeds $1,000 or connects to shared device/card.",
        "R3": "Customer confirms the transaction. Recommend CLOSE_NO_FRAUD.",
        "R4": "No reply within 24 hours. Recommend MONITOR_CARD and DECLINE_TRANSACTION. "
              "Escalate if exposure exceeds $500.",
        "R5": "Card testing. Three or more small online authorizations within an hour, "
              "followed by a larger purchase: DECLINE_TRANSACTION and STEP_UP_AUTH. "
              "If purchase over $100 cleared, BLOCK_CARD.",
        "R6": "Shared origin. When several cards show fraud from the same device/region/email, "
              "CREATE_CASE, FILE_REPORT, and MONITOR_CONNECTED_CARDS.",
        "R7": "Disputed but legitimate. When customer disputes a recurring charge, "
              "CREATE_CASE, VERIFY_WITH_CUSTOMER, WARN_CUSTOMER. Do not block.",
        "R8": "Escalate when uncertain and exposed. If verdict uncertain and exposure > $500, "
              "or evidence conflicts, ESCALATE_TO_ANALYST.",
        "R9": "Undocumented patterns. When activity fits none of the known patterns but shows "
              "coordinated abuse, CREATE_CASE, FILE_REPORT, ESCALATE_TO_ANALYST.",
        "R10": "Never BLOCK_ALL_CARDS unless at least two cards show confirmed fraud or "
               "credentials are confirmed compromised.",
    }

    # Known fraud patterns
    PATTERNS = {
        "card_testing": "A stolen card number is checked before use: three or more tiny "
                       "online authorizations, often under $5, then a larger purchase.",
        "card_not_present_fraud": "The number is used online without the card. Amounts and "
                                  "products that don't fit the cardholder's history, often in "
                                  "a burst of two to four within 48 hours.",
        "card_not_present_new_device": "Same as CNP fraud, with the identity record marking "
                                       "the device as New for this account, sometimes behind a proxy.",
        "out_of_region_use": "Card-present purchases in a billing region the cardholder has "
                             "no history in, while their normal activity continues at home.",
        "account_takeover": "Mixed-channel activity inconsistent with the cardholder, often "
                           "with device and match-flag anomalies, pointing to stolen credentials.",
    }

    def __init__(self, graph: InMemoryGraph):
        self.graph = graph

    def build_context(self, state: InvestigationState) -> str:
        """Build a comprehensive context string for LLM reasoning."""
        sections = []

        # 1. Investigation trigger
        sections.append(self._section_trigger(state))

        # 2. Flagged transaction details
        sections.append(self._section_flagged_transaction(state))

        # 3. Card history analysis
        sections.append(self._section_card_history(state))

        # 4. Device and identity signals
        sections.append(self._section_device_signals(state))

        # 5. Connected entities
        sections.append(self._section_connections(state))

        # 6. Similar closed cases
        sections.append(self._section_prior_cases(state))

        # 7. Relevant policy rules
        sections.append(self._section_policy(state))

        # 8. Pattern definitions
        sections.append(self._section_patterns(state))

        return "\n\n".join(s for s in sections if s)

    def _section_trigger(self, state: InvestigationState) -> str:
        trigger = state.trigger
        return (
            f"## Investigation Trigger\n"
            f"- Case ID: {trigger.case_id}\n"
            f"- Trigger Type: {trigger.trigger_type.value}\n"
            f"- Trigger Text: {trigger.trigger_text}\n"
            f"- Flagged Transaction: {trigger.flagged_txn_id}\n"
            f"- Card: {trigger.card_id}\n"
            f"- Customer: {trigger.customer_id}\n"
            f"- Risk Score: {trigger.risk_score if trigger.risk_score else 'N/A'}"
        )

    def _section_flagged_transaction(self, state: InvestigationState) -> str:
        txn = state.flagged_txn
        if not txn:
            return "## Flagged Transaction\nNot found in graph."

        return (
            f"## Flagged Transaction Details\n"
            f"- ID: {txn.transaction_id}\n"
            f"- Amount: ${txn.amount:.2f}\n"
            f"- Channel: {txn.channel}\n"
            f"- Timestamp: {txn.ts}\n"
            f"- Product Code: {txn.product_cd}\n"
            f"- Billing Region: {txn.addr1}\n"
            f"- Country Code: {txn.addr2}\n"
            f"- Purchaser Email Domain: {txn.p_emaildomain}\n"
            f"- Risk Score: {txn.risk_score:.2f}"
        )

    def _section_card_history(self, state: InvestigationState) -> str:
        txns = state.card_transactions
        if not txns:
            return "## Card History\nNo transaction history found."

        amounts = [t.amount for t in txns]
        avg = sum(amounts) / len(amounts) if amounts else 0
        channels = set(t.channel for t in txns)

        recent = txns[-5:]
        recent_lines = "\n".join(
            f"  - {t.ts}: ${t.amount:.2f} ({t.channel}) risk={t.risk_score:.2f}"
            for t in recent
        )

        return (
            f"## Card History ({len(txns)} transactions)\n"
            f"- Average amount: ${avg:.2f}\n"
            f"- Channels used: {', '.join(channels)}\n"
            f"- Recent transactions:\n{recent_lines}"
        )

    def _section_device_signals(self, state: InvestigationState) -> str:
        if not state.device_neighbors:
            return "## Device Signals\nNo device connections found."

        lines = []
        for dev in state.device_neighbors:
            lines.append(
                f"- Device {dev.get('device_id', 'unknown')}: "
                f"Used by {dev.get('card_count', 0)} cards"
            )
        return "## Device Signals\n" + "\n".join(lines)

    def _section_connections(self, state: InvestigationState) -> str:
        parts = []
        if state.connected_card_ids:
            parts.append(f"- Connected cards: {', '.join(state.connected_card_ids)}")
        if state.connected_transactions:
            parts.append(f"- Connected transactions: {len(state.connected_transactions)}")
        if not parts:
            return "## Connected Entities\nNo significant connections found."
        return "## Connected Entities\n" + "\n".join(parts)

    def _section_prior_cases(self, state: InvestigationState) -> str:
        cases = state.similar_closed_cases
        if not cases:
            return "## Prior Cases\nNo similar cases found in case memory."

        lines = []
        for case in cases[:5]:
            lines.append(
                f"- {case.case_id}: {case.outcome} ({case.pattern}), "
                f"exposure ${case.exposure_usd:.2f}, "
                f"notes: {case.analyst_notes[:100] if case.analyst_notes else 'none'}"
            )
        return "## Prior Cases (Case Memory)\n" + "\n".join(lines)

    def _section_policy(self, state: InvestigationState) -> str:
        """Return the most relevant policy rules."""
        relevant = []
        prob = state.fraud_probability

        if prob < 0.70:
            relevant.append(("R1", self.POLICY_RULES["R1"]))
        if state.pattern.value == "card_testing":
            relevant.append(("R5", self.POLICY_RULES["R5"]))
        if state.device_neighbors:
            relevant.append(("R6", self.POLICY_RULES["R6"]))
        if state.trigger.trigger_type == "customer_report":
            relevant.append(("R2", self.POLICY_RULES["R2"]))
            relevant.append(("R3", self.POLICY_RULES["R3"]))
        if state.verdict.value == "uncertain":
            relevant.append(("R8", self.POLICY_RULES["R8"]))
        if state.pattern.value == "undocumented":
            relevant.append(("R9", self.POLICY_RULES["R9"]))

        if not relevant:
            # Include all rules
            relevant = list(self.POLICY_RULES.items())

        lines = [f"- **{rid}**: {rule}" for rid, rule in relevant]
        return "## Relevant Policy Rules\n" + "\n".join(lines)

    def _section_patterns(self, state: InvestigationState) -> str:
        if state.pattern.value in self.PATTERNS:
            return (
                f"## Matching Pattern\n"
                f"**{state.pattern.value.replace('_', ' ').title()}**: "
                f"{self.PATTERNS[state.pattern.value]}"
            )
        lines = [
            f"- **{name.replace('_', ' ').title()}**: {desc}"
            for name, desc in self.PATTERNS.items()
        ]
        return "## Known Fraud Patterns\n" + "\n".join(lines)
