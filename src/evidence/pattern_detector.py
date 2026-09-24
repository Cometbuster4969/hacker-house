"""
Pattern Detector: identifies fraud patterns from gathered evidence.
Implements detection for the 5 known patterns and undocumented patterns.
"""
from __future__ import annotations
import logging
from collections import defaultdict
from typing import Optional

from ..utils.models import (
    InvestigationState, FraudPattern, Evidence, SourceType,
    Transaction, ClosedCase,
)

logger = logging.getLogger(__name__)


class PatternDetector:
    """Detects fraud patterns from investigation evidence."""

    def detect_patterns(self, state: InvestigationState) -> InvestigationState:
        """Run all pattern detectors and update state."""
        # If already detected during evidence gathering, refine
        if state.pattern != FraudPattern.NONE:
            return state

        # Run detectors in priority order
        detectors = [
            self._detect_card_testing,
            self._detect_cnp_fraud,
            self._detect_cnp_new_device,
            self._detect_out_of_region,
            self._detect_account_takeover,
            self._detect_undocumented,
        ]

        for detector in detectors:
            result = detector(state)
            if result and result != FraudPattern.NONE:
                state.pattern = result
                break

        return state

    def _detect_card_testing(self, state: InvestigationState) -> Optional[FraudPattern]:
        """Pattern 1: Small online authorizations followed by larger purchase."""
        card_txns = state.card_transactions
        if len(card_txns) < 3:
            return None

        # Look for sequence: 3+ small online txns then larger one
        small_count = 0
        large_found = False
        small_total = 0.0

        for txn in card_txns:
            if txn.channel == "online" and txn.amount < 5.0:
                small_count += 1
                small_total += txn.amount
            elif small_count >= 2 and txn.amount >= 50.0:
                large_found = True
                break

        if small_count >= 2 and large_found:
            state.pattern_description = (
                f"Card testing: {small_count} small online authorizations "
                f"(${small_total:.2f} total) followed by a larger purchase. "
                f"Consistent with testing a stolen card number before use."
            )
            return FraudPattern.CARD_TESTING
        return None

    def _detect_cnp_fraud(self, state: InvestigationState) -> Optional[FraudPattern]:
        """Pattern 2: Card-not-present fraud - unusual online activity."""
        if not state.flagged_txn:
            return None

        flagged = state.flagged_txn
        card_txns = state.card_transactions

        if flagged.channel != "online":
            return None

        # Check if amount is unusual compared to history
        if not card_txns:
            return None

        amounts = [t.amount for t in card_txns]
        avg_amount = sum(amounts) / len(amounts) if amounts else 0
        max_historical = max(amounts) if amounts else 0

        # Unusual if flagged amount is significantly higher than history
        if flagged.amount > avg_amount * 3 and flagged.amount > 100:
            # Check for burst pattern (2-4 within 48 hours)
            burst_count = 0
            for txn in card_txns:
                if txn.channel == "online":
                    burst_count += 1

            if burst_count >= 2:
                state.pattern_description = (
                    f"Card-not-present fraud: ${flagged.amount:.2f} online purchase "
                    f"is {flagged.amount/avg_amount:.1f}x the average (${avg_amount:.2f}). "
                    f"Burst of {burst_count} online transactions detected."
                )
                return FraudPattern.CARD_NOT_PRESENT_FRAUD

        return None

    def _detect_cnp_new_device(self, state: InvestigationState) -> Optional[FraudPattern]:
        """Pattern 3: CNP fraud from a new device."""
        if not state.flagged_txn or state.flagged_txn.channel != "online":
            return None

        # Check if device is new for this account
        new_device_found = False
        for ev in state.evidence_collected:
            if "NEW" in ev.claim.upper() and "device" in ev.claim.lower():
                new_device_found = True
                break

        if new_device_found:
            state.pattern_description = (
                f"Card-not-present fraud from a new device. "
                f"Device is marked as New for this account, indicating first-time use."
            )
            return FraudPattern.CARD_NOT_PRESENT_NEW_DEVICE

        # Check for proxy usage
        for ev in state.evidence_collected:
            if "proxy" in ev.claim.lower() and ev.source == SourceType.GRAPH:
                state.pattern_description = (
                    f"Card-not-present fraud from a new device with proxy detected."
                )
                return FraudPattern.CARD_NOT_PRESENT_NEW_DEVICE

        return None

    def _detect_out_of_region(self, state: InvestigationState) -> Optional[FraudPattern]:
        """Pattern 4: Out-of-region use."""
        if not state.flagged_txn:
            return None

        # Check if flagged txn is in-person in a new region
        flagged = state.flagged_txn
        if flagged.channel != "in_person":
            return None

        # Look for evidence of new region
        for ev in state.evidence_collected:
            if "out-of-region" in ev.claim.lower() or "new region" in ev.claim.lower():
                state.pattern_description = (
                    f"Out-of-region use: in-person transaction in billing region "
                    f"{flagged.addr1} which is outside the cardholder's normal area. "
                    f"May indicate a cloned card or physical theft."
                )
                return FraudPattern.OUT_OF_REGION_USE

        return None

    def _detect_account_takeover(self, state: InvestigationState) -> Optional[FraudPattern]:
        """Pattern 5: Account takeover - mixed channel anomalies."""
        if not state.flagged_txn:
            return None

        card_txns = state.card_transactions
        if len(card_txns) < 5:
            return None

        # Look for channel mix anomalies
        channels = set()
        for txn in card_txns[-10:]:
            channels.add(txn.channel)

        if len(channels) > 1:
            # Mixed channel activity
            # Check for device/match anomalies
            has_device_anomaly = False
            for ev in state.evidence_collected:
                if "new" in ev.claim.lower() and "device" in ev.claim.lower():
                    has_device_anomaly = True
                if "proxy" in ev.claim.lower():
                    has_device_anomaly = True

            if has_device_anomaly:
                state.pattern_description = (
                    f"Account takeover: mixed-channel activity ({', '.join(channels)}) "
                    f"with device anomalies suggesting stolen credentials rather than "
                    f"stolen card number."
                )
                return FraudPattern.ACCOUNT_TAKEOVER

        return None

    def _detect_undocumented(self, state: InvestigationState) -> Optional[FraudPattern]:
        """Detect undocumented patterns - coordinated abuse."""
        # Check for shared devices across multiple cards (ring detection)
        if state.device_neighbors:
            for neighbor in state.device_neighbors:
                if neighbor.get("card_count", 0) >= 3:
                    state.pattern_description = (
                        f"Undocumented pattern: coordinated activity across "
                        f"{neighbor['card_count']} cards sharing device "
                        f"{neighbor.get('device_id', 'unknown')}. "
                        f"This may indicate a fraud ring or mule network."
                    )
                    return FraudPattern.UNDOCUMENTED

        # Check for unusual email patterns
        card_txns = state.card_transactions
        if card_txns:
            emails = set()
            for txn in card_txns:
                if txn.r_emaildomain:
                    emails.add(txn.r_emaildomain)

            if len(emails) >= 3:
                state.pattern_description = (
                    f"Undocumented pattern: card used with {len(emails)} different "
                    f"recipient email domains, suggesting potential money movement "
                    f"or account manipulation."
                )
                return FraudPattern.UNDOCUMENTED

        return None

    def calculate_fraud_probability(self, state: InvestigationState) -> float:
        """Calculate fraud probability based on evidence strength."""
        if not state.evidence_collected:
            return 0.5  # No evidence = uncertain

        score = 0.0
        weights = {
            "card_testing": 0.85,
            "card_not_present_fraud": 0.65,
            "card_not_present_new_device": 0.75,
            "out_of_region_use": 0.70,
            "account_takeover": 0.80,
            "undocumented": 0.60,
        }

        # Base score from detected pattern
        if state.pattern != FraudPattern.NONE:
            score = weights.get(state.pattern.value, 0.5)

        # Adjust based on evidence type
        evidence_boosts = {
            "small online authorizations": 0.15,
            "customer denied": 0.20,
            "customer confirmed": -0.30,
            "new device": 0.10,
            "shared device": 0.10,
            "out-of-region": 0.10,
            "high velocity": 0.05,
            "closed case": 0.05,
            "proxy": 0.05,
        }

        for ev in state.evidence_collected:
            claim_lower = ev.claim.lower()
            for keyword, boost in evidence_boosts.items():
                if keyword in claim_lower:
                    score += boost

        # Customer report triggers get a boost
        if state.trigger.trigger_type == "customer_report":
            score += 0.15

        # High risk score is a signal
        if state.trigger.risk_score and state.trigger.risk_score > 0.8:
            score += 0.05

        # Similar confirmed fraud cases boost
        confirmed_fraud_cases = [
            c for c in state.similar_closed_cases
            if c.outcome == "confirmed_fraud"
        ]
        if confirmed_fraud_cases:
            score += 0.10 * min(len(confirmed_fraud_cases), 3)

        # Legitimate-looking patterns reduce score
        for ev in state.evidence_collected:
            if "recurring" in ev.claim.lower() or "matches history" in ev.claim.lower():
                score -= 0.15
            if "normal" in ev.claim.lower() and "pattern" in ev.claim.lower():
                score -= 0.10

        return max(0.0, min(1.0, score))
