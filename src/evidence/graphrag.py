"""
GraphRAG: Retrieval-Augmented Generation using the knowledge graph.
Retrieves evidence from the graph, policy documents, and prior cases,
then synthesizes a structured context for LLM reasoning.
"""
from __future__ import annotations
import logging
from typing import Optional

from ..graph.in_memory_graph import InMemoryGraph
from ..utils.models import InvestigationState, ClosedCase, FraudPattern

logger = logging.getLogger(__name__)


# Full policy text (from the dataset README)
POLICY_TEXT = """
# Bank Fraud Policy v1.0

## Actions
- ALLOW_TRANSACTION: Let the flagged transaction stand
- DECLINE_TRANSACTION: Decline the flagged authorization only. Card stays active
- MONITOR_CARD: Card stays active; raise monitoring sensitivity for 72 hours
- MONITOR_CONNECTED_CARDS: Put other cards linked to same device/region under monitoring
- WARN_CUSTOMER: Send an informational message
- VERIFY_WITH_CUSTOMER: Ask the cardholder whether they made the transaction
- STEP_UP_AUTH: Require a one-time passcode or app confirmation
- BLOCK_CARD: Block this card and reissue
- BLOCK_ALL_CARDS: Block every card the customer holds
- GENERATE_REPORT: Write up the investigation without opening a case
- CREATE_CASE: Open an internal fraud case with evidence attached
- FILE_REPORT: File a suspicious activity report with the regulator
- ESCALATE_TO_ANALYST: Hand the case to a human analyst
- CLOSE_NO_FRAUD: Close the alert as legitimate

## Approval Routes
- auto: ALLOW_TRANSACTION, MONITOR_CARD, MONITOR_CONNECTED_CARDS, WARN_CUSTOMER, 
        VERIFY_WITH_CUSTOMER, STEP_UP_AUTH, GENERATE_REPORT, CREATE_CASE, 
        ESCALATE_TO_ANALYST, CLOSE_NO_FRAUD
- L1 (team lead): DECLINE_TRANSACTION; BLOCK_CARD when exposure <= $2,500
- L2 (fraud manager): BLOCK_CARD when exposure > $2,500; BLOCK_ALL_CARDS always; FILE_REPORT always

## Rules

R1: Verify before you block on a weak signal. If the case rests on a single signal 
    and fraud probability is below 0.70, recommend VERIFY_WITH_CUSTOMER or STEP_UP_AUTH 
    before any block. Blocking a legitimate customer on one signal is a policy breach.

R2: Customer denies the transaction. Recommend BLOCK_CARD and CREATE_CASE. Add FILE_REPORT 
    if exposure exceeds $1,000 or the case connects to a shared device profile or another card's fraud.

R3: Customer confirms the transaction. Recommend CLOSE_NO_FRAUD. Note the confirmation.

R4: No reply within 24 hours. Recommend MONITOR_CARD and DECLINE_TRANSACTION. 
    Escalate if exposure exceeds $500.

R5: Card testing. Three or more small online authorizations on one card within an hour, 
    followed by a larger purchase: recommend DECLINE_TRANSACTION and STEP_UP_AUTH. 
    If a purchase over $100 has already cleared, recommend BLOCK_CARD.

R6: Shared origin. When several cards show fraud from the same device profile, billing region, 
    or recipient email in one window, name the shared element, recommend CREATE_CASE and FILE_REPORT, 
    and MONITOR_CONNECTED_CARDS for every card that shares it.

R7: Disputed but legitimate. When the customer disputes a charge that matches their own 
    recurring pattern (same merchant, same amount, monthly), recommend CREATE_CASE, 
    VERIFY_WITH_CUSTOMER, and WARN_CUSTOMER. Do not block.

R8: Escalate when uncertain and exposed. If the verdict is uncertain and exposure exceeds $500, 
    or the evidence conflicts, recommend ESCALATE_TO_ANALYST.

R9: Undocumented patterns. When activity fits none of the known patterns but shows coordinated 
    or repeated abuse across customers, recommend CREATE_CASE, FILE_REPORT, and ESCALATE_TO_ANALYST, 
    and describe the pattern in your own words.

R10: Never BLOCK_ALL_CARDS unless at least two of the customer's cards show confirmed fraud 
     or the customer's credentials are confirmed compromised.

## Case vs Report
- A case (CREATE_CASE) is the bank's internal record. Open when probability >= 0.30, 
  when you request evidence, or when a customer disputes.
- A SAR (FILE_REPORT) is a regulatory filing. File when fraud is confirmed/strongly suspected AND:
  exposure > $1,000; OR connects to shared device/region/other fraud; OR coordinated/undocumented pattern.

## Stopping Rules
Stop when:
- Fraud probability >= 0.85 or <= 0.15, supported by at least 2 independent pieces of evidence
- A verification response settles the question
- Further steps are unlikely to change the decision
"""

PATTERNS_TEXT = """
# Known Fraud Patterns

1. Card testing: A stolen card number is checked before use: three or more tiny online 
   authorizations, often under $5, then a larger purchase. Confirmed by the sequence itself.

2. Card-not-present fraud: The number is used online without the card. Amounts and products 
   that don't fit the cardholder's history, often in a burst of two to four within 48 hours.

3. Card-not-present fraud from a new device: Same as above, with the identity record marking 
   the device as New for this account, sometimes behind a proxy. Stronger than pattern 2.

4. Out-of-region use: Card-present purchases in a billing region the cardholder has no history in, 
   while their normal activity continues at home. Several days in one new region is a trip, not fraud.

5. Account takeover: Mixed-channel activity inconsistent with the cardholder, often with device 
   and match-flag anomalies, pointing to stolen credentials rather than a stolen number.
"""


class GraphRAGSynthesizer:
    """
    Synthesizes context from the knowledge graph + policy documents for LLM reasoning.
    This is the "RAG" in GraphRAG — it retrieves relevant evidence from the graph
    and documents, then formats it as context for the LLM.
    """

    def __init__(self, graph: InMemoryGraph):
        self.graph = graph

    def build_investigation_context(self, state: InvestigationState) -> str:
        """Build comprehensive context for LLM reasoning."""
        sections = []

        # 1. Trigger and flagged transaction
        sections.append(self._section_trigger(state))

        # 2. Transaction history analysis
        sections.append(self._section_transaction_analysis(state))

        # 3. Device and identity signals
        sections.append(self._section_device_analysis(state))

        # 4. Connected entities (other cards, devices, regions)
        sections.append(self._section_connections(state))

        # 5. Prior cases (case memory)
        sections.append(self._section_case_memory(state))

        # 6. Graph-level signals
        sections.append(self._section_graph_signals(state))

        return "\n\n".join(s for s in sections if s)

    def build_policy_context(self, state: InvestigationState) -> str:
        """Build policy context relevant to this investigation."""
        sections = [POLICY_TEXT]

        # Add pattern-specific guidance
        if state.pattern != FraudPattern.NONE:
            sections.append(f"\n## Potentially Matching Pattern\n{PATTERNS_TEXT}")
        else:
            sections.append(f"\n## All Known Patterns\n{PATTERNS_TEXT}")

        return "\n\n".join(sections)

    def _section_trigger(self, state: InvestigationState) -> str:
        trigger = state.trigger
        flagged = state.flagged_txn

        lines = [f"## Trigger & Flagged Transaction"]
        lines.append(f"- Case: {trigger.case_id}")
        lines.append(f"- Trigger type: {trigger.trigger_type.value}")
        lines.append(f"- Trigger text: {trigger.trigger_text}")
        lines.append(f"- Card: {trigger.card_id}")
        lines.append(f"- Customer: {trigger.customer_id}")

        if trigger.risk_score is not None:
            lines.append(f"- Risk score: {trigger.risk_score:.2f}")

        if flagged:
            lines.append(f"\n### Flagged Transaction Details")
            lines.append(f"- ID: {flagged.transaction_id}")
            lines.append(f"- Amount: ${flagged.amount:.2f}")
            lines.append(f"- Channel: {flagged.channel}")
            lines.append(f"- Timestamp: {flagged.timestamp}")
            lines.append(f"- Product code: {flagged.product_cd}")
            lines.append(f"- Billing region: {flagged.addr1}")
            lines.append(f"- Purchaser email: {flagged.p_emaildomain}")
            lines.append(f"- Risk score: {flagged.risk_score:.2f}")

        return "\n".join(lines)

    def _section_transaction_analysis(self, state: InvestigationState) -> str:
        txns = state.card_transactions
        if not txns:
            return "## Transaction History\nNo transaction history found for this card."

        amounts = [t.amount for t in txns]
        channels = {}
        for t in txns:
            channels[t.channel] = channels.get(t.channel, 0) + 1

        avg = sum(amounts) / len(amounts) if amounts else 0
        max_amt = max(amounts) if amounts else 0

        lines = [f"## Transaction History ({len(txns)} transactions)"]
        lines.append(f"- Average amount: ${avg:.2f}")
        lines.append(f"- Max amount: ${max_amt:.2f}")
        lines.append(f"- Channels: {channels}")

        # Show recent transactions
        lines.append(f"\n### Recent Transactions (last 10)")
        for t in txns[-10:]:
            marker = " ⚠️ FLAGGED" if t.transaction_id == state.trigger.flagged_txn_id else ""
            lines.append(
                f"  - {t.transaction_id}: ${t.amount:.2f} ({t.channel}) "
                f"at {t.timestamp} risk={t.risk_score:.2f}{marker}"
            )

        # Velocity analysis
        if len(txns) >= 3:
            recent_amounts = [t.amount for t in txns[-5:]]
            if any(a < 5.0 for a in recent_amounts):
                small_count = sum(1 for a in recent_amounts if a < 5.0)
                lines.append(f"\n### ⚠️ Velocity Alert: {small_count} small transactions in recent history")

        return "\n".join(lines)

    def _section_device_analysis(self, state: InvestigationState) -> str:
        if not state.device_neighbors and not state.identity_records:
            return "## Device Signals\nNo device information available (possibly in-person transaction)."

        lines = ["## Device & Identity Signals"]

        if state.flagged_txn and state.flagged_txn.channel == "online":
            lines.append("- Channel: Online (device record expected)")

        for dev in state.device_neighbors:
            lines.append(f"\n### Device: {dev.get('device_id', 'unknown')}")
            lines.append(f"- Info: {dev.get('device_info', 'unknown')}")
            lines.append(f"- OS: {dev.get('os', 'unknown')}")
            lines.append(f"- Browser: {dev.get('browser', 'unknown')}")
            lines.append(f"- Cards seen: {dev.get('card_count', 0)}")
            if dev.get("card_count", 0) > 1:
                lines.append(f"- ⚠️ SHARED DEVICE: {dev.get('card_count', 0)} cards on same device")
                for card in dev.get("cards", []):
                    lines.append(f"  - Card: {card}")

        return "\n".join(lines)

    def _section_connections(self, state: InvestigationState) -> str:
        parts = ["## Connected Entities"]

        if state.connected_card_ids:
            parts.append(f"### Connected Cards: {', '.join(state.connected_card_ids)}")
            parts.append("These cards share a device profile or other signal with the flagged card.")

        if state.connected_transactions:
            parts.append(f"\n### Connected Transactions ({len(state.connected_transactions)})")
            for t in state.connected_transactions[:5]:
                parts.append(
                    f"  - {t.transaction_id}: ${t.amount:.2f} on card {t.card_id} "
                    f"({t.channel}) at {t.timestamp}"
                )

        if state.region_neighbors:
            parts.append(f"\n### Region Connections")
            for region in state.region_neighbors:
                parts.append(f"  - Region {region.get('region_id')}: {region.get('card_count', 0)} cards")

        if state.email_neighbors:
            parts.append(f"\n### Email Connections")
            for email in state.email_neighbors:
                parts.append(f"  - {email.get('domain')}: {email.get('card_count', 0)} cards")

        if not state.connected_card_ids and not state.connected_transactions:
            parts.append("No significant connections to other cards or entities found.")

        return "\n".join(parts)

    def _section_case_memory(self, state: InvestigationState) -> str:
        cases = state.similar_closed_cases
        if not cases:
            return "## Case Memory (Prior Cases)\nNo similar prior cases found."

        lines = ["## Case Memory (Prior Cases)"]
        lines.append(f"Found {len(cases)} similar prior cases:\n")

        for case in cases[:5]:
            lines.append(f"### {case.case_id}")
            lines.append(f"- Outcome: {case.outcome}")
            lines.append(f"- Pattern: {case.pattern}")
            lines.append(f"- Exposure: ${case.exposure_usd:.2f}")
            lines.append(f"- Transactions: {case.n_txns}")
            if case.analyst_notes:
                lines.append(f"- Analyst notes: {case.analyst_notes[:200]}")
            lines.append("")

        return "\n".join(lines)

    def _section_graph_signals(self, state: InvestigationState) -> str:
        """Extract high-level graph signals."""
        lines = ["## Graph-Level Signals"]

        # Check if flagged txn is an outlier
        if state.flagged_txn and state.card_transactions:
            amounts = [t.amount for t in state.card_transactions]
            avg = sum(amounts) / len(amounts)
            if avg > 0:
                ratio = state.flagged_txn.amount / avg
                if ratio > 3:
                    lines.append(
                        f"- ⚠️ Flagged amount ${state.flagged_txn.amount:.2f} is "
                        f"{ratio:.1f}x the card average ${avg:.2f}"
                    )
                elif ratio < 0.5:
                    lines.append(
                        f"- Flagged amount ${state.flagged_txn.amount:.2f} is "
                        f"below average ${avg:.2f} (possible testing)"
                    )

        # Check for burst patterns
        if len(state.card_transactions) >= 3:
            recent = state.card_transactions[-5:]
            if len(recent) >= 3:
                time_gaps = []
                for i in range(1, len(recent)):
                    # Simple check - if timestamps are close
                    if recent[i].timestamp[:10] == recent[i-1].timestamp[:10]:
                        time_gaps.append(recent[i].transaction_id)
                if len(time_gaps) >= 2:
                    lines.append(
                        f"- ⚠️ Burst pattern: {len(time_gaps)}+ transactions on same day"
                    )

        if len(lines) == 1:
            lines.append("No unusual graph-level signals detected.")

        return "\n".join(lines)
