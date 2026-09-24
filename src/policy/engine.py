"""
Policy Engine: enforces the bank's fraud policy rules R1-R10.
Determines actions, approval routes, and SAR requirements.
"""
from __future__ import annotations
import logging
from typing import Optional

from ..utils.models import (
    InvestigationState, Verdict, FraudPattern, CaseStatus,
    ActionType, ApprovalRoute, ActionRecommendation,
    NextBestActions, SuspiciousActivityReport, EvidenceRequest,
)

logger = logging.getLogger(__name__)


# Policy thresholds from the dataset
THRESHOLDS = {
    "verify_below": 0.70,      # R1: verify before blocking below this
    "high_confidence": 0.85,    # Stopping rule
    "low_confidence": 0.15,     # Stopping rule
    "sar_exposure": 1000.0,     # R2: SAR threshold
    "escalate_exposure": 500.0, # R8: escalation threshold
    "l2_exposure": 2500.0,      # L2 approval threshold
    "block_all_min_cards": 2,   # R10: min confirmed fraud cards
}


class PolicyEngine:
    """Evaluates investigation state against the fraud policy."""

    def evaluate_initial_actions(self, state: InvestigationState) -> NextBestActions:
        """Determine initial next-best-actions based on current evidence."""
        actions = []

        fraud_prob = state.fraud_probability
        pattern = state.pattern
        trigger = state.trigger

        # R1: Verify before blocking on weak signal
        if fraud_prob < THRESHOLDS["verify_below"]:
            if trigger.trigger_type == "customer_report":
                # Customer reports get priority verification
                actions.append(ActionRecommendation(
                    action=ActionType.VERIFY_WITH_CUSTOMER,
                    route=ApprovalRoute.AUTO,
                    reason="R1: Customer report requires verification before any blocking action",
                ))
            elif fraud_prob >= 0.30:
                actions.append(ActionRecommendation(
                    action=ActionType.VERIFY_WITH_CUSTOMER,
                    route=ApprovalRoute.AUTO,
                    reason=f"R1: Probability {fraud_prob:.2f} below 0.70 threshold, "
                           f"verify before blocking",
                ))
                actions.append(ActionRecommendation(
                    action=ActionType.CREATE_CASE,
                    route=ApprovalRoute.AUTO,
                    reason=f"R1/3a: Probability {fraud_prob:.2f} warrants a case",
                ))
            else:
                actions.append(ActionRecommendation(
                    action=ActionType.MONITOR_CARD,
                    route=ApprovalRoute.AUTO,
                    reason=f"R1: Low probability {fraud_prob:.2f}, monitor for now",
                ))

        # R5: Card testing
        elif pattern == FraudPattern.CARD_TESTING:
            # Check if large purchase already cleared
            has_cleared_large = any(
                t.amount >= 100 for t in state.card_transactions[-5:]
            )
            if has_cleared_large:
                actions.append(ActionRecommendation(
                    action=ActionType.BLOCK_CARD,
                    route=ApprovalRoute.L1,
                    reason="R5: Card testing with purchase over $100 already cleared",
                ))
            else:
                actions.append(ActionRecommendation(
                    action=ActionType.DECLINE_TRANSACTION,
                    route=ApprovalRoute.L1,
                    reason="R5: Testing sequence observed",
                ))
                actions.append(ActionRecommendation(
                    action=ActionType.STEP_UP_AUTH,
                    route=ApprovalRoute.AUTO,
                    reason="R5: Require authentication before further activity",
                ))
            actions.append(ActionRecommendation(
                action=ActionType.CREATE_CASE,
                route=ApprovalRoute.AUTO,
                reason="R5: Card testing pattern requires case",
            ))

        # High probability fraud
        elif fraud_prob >= THRESHOLDS["high_confidence"]:
            exposure = state.flagged_txn.amount if state.flagged_txn else 0
            if exposure > THRESHOLDS["l2_exposure"]:
                actions.append(ActionRecommendation(
                    action=ActionType.BLOCK_CARD,
                    route=ApprovalRoute.L2,
                    reason=f"R2: High confidence fraud with exposure ${exposure:.2f} > $2,500",
                ))
            else:
                actions.append(ActionRecommendation(
                    action=ActionType.BLOCK_CARD,
                    route=ApprovalRoute.L1,
                    reason=f"R2: High confidence fraud with exposure ${exposure:.2f}",
                ))
            actions.append(ActionRecommendation(
                action=ActionType.CREATE_CASE,
                route=ApprovalRoute.AUTO,
                reason="R2: Confirmed fraud requires case",
            ))

        # Medium probability (0.70 - 0.85)
        elif fraud_prob >= THRESHOLDS["verify_below"]:
            actions.append(ActionRecommendation(
                action=ActionType.VERIFY_WITH_CUSTOMER,
                route=ApprovalRoute.AUTO,
                reason=f"R1: Probability {fraud_prob:.2f}, verify before blocking",
            ))
            actions.append(ActionRecommendation(
                action=ActionType.CREATE_CASE,
                route=ApprovalRoute.AUTO,
                reason=f"R1/3a: Probability {fraud_prob:.2f} warrants a case",
            ))

        # R8: Escalate when uncertain and exposed
        if state.verdict == Verdict.UNCERTAIN:
            exposure = state.flagged_txn.amount if state.flagged_txn else 0
            if exposure > THRESHOLDS["escalate_exposure"]:
                actions.append(ActionRecommendation(
                    action=ActionType.ESCALATE_TO_ANALYST,
                    route=ApprovalRoute.AUTO,
                    reason=f"R8: Verdict uncertain with exposure ${exposure:.2f} > $500",
                ))

        # If no actions determined, default to monitor
        if not actions:
            actions.append(ActionRecommendation(
                action=ActionType.MONITOR_CARD,
                route=ApprovalRoute.AUTO,
                reason="Default: No strong signals, monitoring recommended",
            ))

        return NextBestActions(
            initial=actions,
            final=actions,  # Will be updated after evidence gathering
            what_changed="nothing",
        )

    def evaluate_final_actions(self, state: InvestigationState,
                                evidence_requests: list[EvidenceRequest]) -> NextBestActions:
        """Determine final actions after all evidence (including requested) is gathered."""
        initial_actions = self.evaluate_initial_actions(state)
        final_actions = []

        fraud_prob = state.fraud_probability
        pattern = state.pattern
        exposure = sum(t.amount for t in self._get_affected_txns(state))

        # R2: Customer denies the transaction
        customer_denied = self._check_customer_denied(evidence_requests)
        customer_confirmed = self._check_customer_confirmed(evidence_requests)

        if customer_confirmed:
            final_actions.append(ActionRecommendation(
                action=ActionType.CLOSE_NO_FRAUD,
                route=ApprovalRoute.AUTO,
                reason="R3: Customer confirmed the transaction as legitimate",
            ))
            state.verdict = Verdict.LEGITIMATE
            state.fraud_probability = max(0.0, fraud_prob - 0.5)

        elif customer_denied:
            if exposure <= THRESHOLDS["l2_exposure"]:
                final_actions.append(ActionRecommendation(
                    action=ActionType.BLOCK_CARD,
                    route=ApprovalRoute.L1,
                    reason=f"R2: Customer denied; exposure ${exposure:.2f} ≤ $2,500",
                ))
            else:
                final_actions.append(ActionRecommendation(
                    action=ActionType.BLOCK_CARD,
                    route=ApprovalRoute.L2,
                    reason=f"R2: Customer denied; exposure ${exposure:.2f} > $2,500",
                ))
            final_actions.append(ActionRecommendation(
                action=ActionType.CREATE_CASE,
                route=ApprovalRoute.AUTO,
                reason="R2: Customer denial confirms fraud",
            ))

            # R2: FILE_REPORT if exposure > $1000 or shared device/card
            needs_report = (
                exposure > THRESHOLDS["sar_exposure"] or
                len(state.device_neighbors) > 0 or
                len(state.connected_card_ids) > 0
            )
            if needs_report:
                final_actions.append(ActionRecommendation(
                    action=ActionType.FILE_REPORT,
                    route=ApprovalRoute.L2,
                    reason=f"R2: Exposure ${exposure:.2f} > $1,000 or shared connections",
                ))

            # Monitor connected cards
            if state.device_neighbors:
                final_actions.append(ActionRecommendation(
                    action=ActionType.MONITOR_CONNECTED_CARDS,
                    route=ApprovalRoute.AUTO,
                    reason="R6: Shared device profile detected, monitoring connected cards",
                ))

            state.verdict = Verdict.FRAUD
            state.fraud_probability = min(1.0, fraud_prob + 0.2)

        else:
            # No customer response - use evidence-based assessment
            if fraud_prob >= THRESHOLDS["high_confidence"]:
                final_actions = self._high_confidence_actions(state, exposure)
            elif fraud_prob >= THRESHOLDS["verify_below"]:
                final_actions = self._medium_confidence_actions(state, exposure)
            elif fraud_prob >= THRESHOLDS["low_confidence"]:
                final_actions = initial_actions.initial
            else:
                final_actions.append(ActionRecommendation(
                    action=ActionType.CLOSE_NO_FRAUD,
                    route=ApprovalRoute.AUTO,
                    reason=f"Low fraud probability {fraud_prob:.2f} < 0.15, "
                           f"insufficient evidence of fraud",
                ))
                state.verdict = Verdict.LEGITIMATE

        # R7: Disputed but legitimate
        if (state.trigger.trigger_type == "customer_report" and
                fraud_prob < 0.30):
            # Check if it's a recurring pattern
            is_recurring = self._check_recurring_pattern(state)
            if is_recurring:
                final_actions = [
                    ActionRecommendation(
                        action=ActionType.CREATE_CASE,
                        route=ApprovalRoute.AUTO,
                        reason="R7: Disputed charge that matches recurring pattern",
                    ),
                    ActionRecommendation(
                        action=ActionType.VERIFY_WITH_CUSTOMER,
                        route=ApprovalRoute.AUTO,
                        reason="R7: Verify with customer",
                    ),
                    ActionRecommendation(
                        action=ActionType.WARN_CUSTOMER,
                        route=ApprovalRoute.AUTO,
                        reason="R7: Warn customer about recurring charges",
                    ),
                ]
                state.verdict = Verdict.LEGITIMATE

        # R9: Undocumented patterns
        if pattern == FraudPattern.UNDOCUMENTED:
            has_create = any(a.action == ActionType.CREATE_CASE for a in final_actions)
            has_report = any(a.action == ActionType.FILE_REPORT for a in final_actions)
            has_escalate = any(a.action == ActionType.ESCALATE_TO_ANALYST for a in final_actions)

            if not has_create:
                final_actions.append(ActionRecommendation(
                    action=ActionType.CREATE_CASE,
                    route=ApprovalRoute.AUTO,
                    reason="R9: Undocumented pattern requires case",
                ))
            if not has_report and exposure > THRESHOLDS["sar_exposure"]:
                final_actions.append(ActionRecommendation(
                    action=ActionType.FILE_REPORT,
                    route=ApprovalRoute.L2,
                    reason="R9: Undocumented coordinated pattern requires SAR",
                ))
            if not has_escalate:
                final_actions.append(ActionRecommendation(
                    action=ActionType.ESCALATE_TO_ANALYST,
                    route=ApprovalRoute.AUTO,
                    reason="R9: Undocumented pattern needs analyst review",
                ))

        # R10: Never BLOCK_ALL_CARDS without confirmed multi-card compromise
        block_all = any(a.action == ActionType.BLOCK_ALL_CARDS for a in final_actions)
        if block_all:
            fraud_cards = len([
                c for c in state.connected_card_ids
                if self._card_has_confirmed_fraud(state, c)
            ])
            if fraud_cards < THRESHOLDS["block_all_min_cards"]:
                final_actions = [
                    a for a in final_actions
                    if a.action != ActionType.BLOCK_ALL_CARDS
                ]
                logger.info("R10: Removed BLOCK_ALL_CARDS - only %d confirmed fraud cards", fraud_cards)

        # Deduplicate
        final_actions = self._deduplicate_actions(final_actions)

        what_changed = self._describe_changes(initial_actions.initial, final_actions)

        return NextBestActions(
            initial=initial_actions.initial,
            final=final_actions,
            what_changed=what_changed,
        )

    def evaluate_sar(self, state: InvestigationState) -> SuspiciousActivityReport:
        """Determine if a SAR should be filed."""
        if state.verdict != Verdict.FRAUD:
            return SuspiciousActivityReport(file=False, reason="Verdict is not fraud")

        exposure = sum(t.amount for t in self._get_affected_txns(state))

        should_file = False
        reason = ""

        # Exposure > $1,000
        if exposure > THRESHOLDS["sar_exposure"]:
            should_file = True
            reason = f"Exposure ${exposure:.2f} exceeds $1,000 threshold"

        # Shared device profile or region cluster
        if state.device_neighbors:
            should_file = True
            reason = "Shared device profile linking multiple cards"

        # Connected to other customer's fraud
        if state.connected_card_ids:
            should_file = True
            reason = "Connected to another card's fraud"

        # Coordinated or undocumented pattern
        if state.pattern == FraudPattern.UNDOCUMENTED:
            should_file = True
            reason = "Undocumented coordinated pattern"

        if not should_file:
            return SuspiciousActivityReport(
                file=False,
                reason="No SAR triggers met: exposure below threshold and no shared connections",
            )

        # Build narrative
        narrative = self._build_sar_narrative(state, exposure)

        # Get activity dates
        txns = self._get_affected_txns(state)
        dates = sorted(set(t.timestamp[:10] for t in txns if t.timestamp))

        return SuspiciousActivityReport(
            file=True,
            reason=reason,
            narrative=narrative,
            subjects=self._get_sar_subjects(state),
            total_amount_usd=exposure,
            activity_dates=[dates[0], dates[-1]] if dates else [],
        )

    def should_stop_investigation(self, state: InvestigationState) -> tuple[bool, str]:
        """Determine if enough evidence exists to stop."""
        prob = state.fraud_probability

        # High confidence - stop
        if prob >= THRESHOLDS["high_confidence"]:
            evidence_count = len([
                e for e in state.evidence_collected
                if e.source.value == "graph"
            ])
            if evidence_count >= 2:
                return True, (
                    f"Fraud probability {prob:.2f} ≥ 0.85 supported by "
                    f"{evidence_count} independent pieces of evidence"
                )

        # Low confidence - stop
        if prob <= THRESHOLDS["low_confidence"]:
            evidence_count = len(state.evidence_collected)
            if evidence_count >= 2:
                return True, (
                    f"Fraud probability {prob:.2f} ≤ 0.15 with "
                    f"{evidence_count} pieces of evidence showing no fraud indicators"
                )

        # Customer response settles it
        for req in state.evidence_requests:
            if req.assumed_response:
                if "denied" in req.assumed_response.lower():
                    return True, "Customer denial settled the verdict"
                if "confirm" in req.assumed_response.lower():
                    return True, "Customer confirmation settled the verdict"

        # Max steps reached
        if state.steps_completed >= 8:
            return True, "Maximum investigation steps reached"

        return False, ""

    def determine_status(self, state: InvestigationState) -> CaseStatus:
        """Determine the final case status."""
        if state.verdict == Verdict.FRAUD:
            return CaseStatus.CLOSED_FRAUD
        elif state.verdict == Verdict.LEGITIMATE:
            return CaseStatus.CLOSED_LEGITIMATE
        elif state.verdict == Verdict.UNCERTAIN:
            if state.fraud_probability >= 0.50:
                return CaseStatus.ESCALATED
            return CaseStatus.OPEN
        return CaseStatus.OPEN

    def _high_confidence_actions(self, state: InvestigationState,
                                  exposure: float) -> list[ActionRecommendation]:
        """Actions for high-confidence fraud assessment."""
        actions = []

        if exposure > THRESHOLDS["l2_exposure"]:
            actions.append(ActionRecommendation(
                action=ActionType.BLOCK_CARD,
                route=ApprovalRoute.L2,
                reason=f"High confidence fraud, exposure ${exposure:.2f} > $2,500",
            ))
        else:
            actions.append(ActionRecommendation(
                action=ActionType.BLOCK_CARD,
                route=ApprovalRoute.L1,
                reason=f"High confidence fraud, exposure ${exposure:.2f}",
            ))

        actions.append(ActionRecommendation(
            action=ActionType.CREATE_CASE,
            route=ApprovalRoute.AUTO,
            reason="Confirmed fraud requires case creation",
        ))

        if exposure > THRESHOLDS["sar_exposure"] or state.device_neighbors:
            actions.append(ActionRecommendation(
                action=ActionType.FILE_REPORT,
                route=ApprovalRoute.L2,
                reason=f"SAR required: exposure ${exposure:.2f} or shared connections",
            ))

        if state.device_neighbors:
            actions.append(ActionRecommendation(
                action=ActionType.MONITOR_CONNECTED_CARDS,
                route=ApprovalRoute.AUTO,
                reason="Shared device profile detected",
            ))

        state.verdict = Verdict.FRAUD
        return actions

    def _medium_confidence_actions(self, state: InvestigationState,
                                    exposure: float) -> list[ActionRecommendation]:
        """Actions for medium-confidence assessment."""
        actions = [
            ActionRecommendation(
                action=ActionType.VERIFY_WITH_CUSTOMER,
                route=ApprovalRoute.AUTO,
                reason=f"R1: Probability {state.fraud_probability:.2f}, verify before blocking",
            ),
            ActionRecommendation(
                action=ActionType.CREATE_CASE,
                route=ApprovalRoute.AUTO,
                reason=f"Case warranted at probability {state.fraud_probability:.2f}",
            ),
        ]

        if exposure > THRESHOLDS["escalate_exposure"]:
            actions.append(ActionRecommendation(
                action=ActionType.ESCALATE_TO_ANALYST,
                route=ApprovalRoute.AUTO,
                reason=f"R8: Medium confidence with exposure ${exposure:.2f}",
            ))

        return actions

    def _get_affected_txns(self, state: InvestigationState) -> list:
        """Get transactions identified as part of the fraud episode."""
        affected = []
        if state.flagged_txn:
            affected.append(state.flagged_txn)
        # Add connected transactions
        for txn in state.connected_transactions:
            if txn.transaction_id not in [t.transaction_id for t in affected]:
                affected.append(txn)
        return affected

    def _check_customer_denied(self, requests: list[EvidenceRequest]) -> bool:
        """Check if customer denied the transaction."""
        for req in requests:
            if req.type == "customer_validation" and "denied" in req.assumed_response.lower():
                return True
        return False

    def _check_customer_confirmed(self, requests: list[EvidenceRequest]) -> bool:
        """Check if customer confirmed the transaction."""
        for req in requests:
            if req.type == "customer_validation" and "confirm" in req.assumed_response.lower():
                return True
        return False

    def _check_recurring_pattern(self, state: InvestigationState) -> bool:
        """Check if the flagged transaction matches a recurring pattern."""
        if not state.flagged_txn:
            return False

        flagged = state.flagged_txn
        # Check for same merchant/amount in history
        for txn in state.card_transactions:
            if (txn.p_emaildomain == flagged.p_emaildomain and
                abs(txn.amount - flagged.amount) < 1.0 and
                txn.transaction_id != flagged.transaction_id):
                return True
        return False

    def _card_has_confirmed_fraud(self, state: InvestigationState,
                                   card_id: str) -> bool:
        """Check if a card has confirmed fraud in closed cases."""
        for case in state.similar_closed_cases:
            if case.card_id == card_id and case.outcome == "confirmed_fraud":
                return True
        return False

    def _build_sar_narrative(self, state: InvestigationState,
                              exposure: float) -> str:
        """Build a SAR narrative that stands on its own."""
        trigger = state.trigger
        flagged = state.flagged_txn

        # Who
        who = (f"Customer {trigger.customer_id}, card {trigger.card_id}")

        # What
        what_parts = []
        if state.pattern != FraudPattern.NONE and state.pattern != FraudPattern.UNDOCUMENTED:
            what_parts.append(f"{state.pattern.value.replace('_', ' ')}")
        if flagged:
            what_parts.append(f"${flagged.amount:.2f} {flagged.channel} transaction")

        what = ", ".join(what_parts) if what_parts else "suspicious activity"

        # When
        when = flagged.timestamp[:10] if flagged and flagged.timestamp else trigger.opened_at[:10]

        # Where
        where_parts = []
        if flagged:
            if flagged.channel == "online":
                where_parts.append("online")
            else:
                where_parts.append(f"in-person in billing region {flagged.addr1}")
        where = ", ".join(where_parts) if where_parts else "unknown location"

        # How
        how_parts = []
        if state.pattern_description:
            how_parts.append(state.pattern_description)
        if state.device_neighbors:
            how_parts.append(
                f"Activity linked to device shared across "
                f"{state.device_neighbors[0].get('card_count', 0)} cards"
            )
        how = ". ".join(how_parts) if how_parts else "Under investigation"

        # Why suspicious
        why_parts = []
        for ev in state.evidence_collected[:5]:
            why_parts.append(ev.claim)
        why = ". ".join(why_parts) if why_parts else "Multiple indicators of unauthorized activity"

        narrative = (
            f"On {when}, {who} was flagged for {what}. "
            f"Location: {where}. "
            f"{how}. "
            f"The activity is suspicious because: {why}. "
            f"Total exposure: ${exposure:.2f}. "
            f"Investigation pattern: {state.pattern.value}."
        )

        # Add connected entities
        if state.connected_card_ids:
            narrative += (
                f" Connected cards: {', '.join(state.connected_card_ids)}."
            )

        # Add prior cases
        if state.similar_closed_cases:
            case_refs = [c.case_id for c in state.similar_closed_cases[:3]]
            narrative += f" Similar prior cases: {', '.join(case_refs)}."

        return narrative

    def _get_sar_subjects(self, state: InvestigationState) -> list[str]:
        """Get list of subjects for the SAR."""
        subjects = [state.trigger.customer_id, state.trigger.card_id]
        subjects.extend(state.connected_card_ids)
        for neighbor in state.device_neighbors:
            subjects.append(neighbor.get("device_id", ""))
        return [s for s in subjects if s]

    def _describe_changes(self, initial: list[ActionRecommendation],
                           final: list[ActionRecommendation]) -> str:
        """Describe what changed between initial and final actions."""
        initial_set = {a.action for a in initial}
        final_set = {a.action for a in final}

        added = final_set - initial_set
        removed = initial_set - final_set

        if not added and not removed:
            return "nothing"

        parts = []
        if added:
            parts.append(f"Added: {', '.join(a.value for a in added)}")
        if removed:
            parts.append(f"Removed: {', '.join(a.value for a in removed)}")
        return "; ".join(parts)

    def _deduplicate_actions(self, actions: list[ActionRecommendation]) -> list[ActionRecommendation]:
        """Remove duplicate action types, keeping the first occurrence."""
        seen = set()
        result = []
        for action in actions:
            if action.action not in seen:
                seen.add(action.action)
                result.append(action)
        return result
