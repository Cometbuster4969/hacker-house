"""
Agent Orchestrator: drives the end-to-end investigation flow.
Implements the 8-step investigation cycle:
  Trigger → Investigate → Gather Evidence → Assess Uncertainty →
  Gather More Evidence → Take Actions → Explain → Update Memory
"""
from __future__ import annotations
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..graph.in_memory_graph import InMemoryGraph
from ..graph.data_loader import DataLoader
from ..evidence.gatherer import EvidenceGatherer
from ..evidence.pattern_detector import PatternDetector
from ..policy.engine import PolicyEngine
from ..utils.models import (
    CasePackEntry, CaseAnswer, InvestigationCase, InvestigationState,
    CaseStatus, Verdict, FraudPattern, EvidenceRequest, SourceType,
    ActionType, ApprovalRoute, ActionRecommendation,
)

logger = logging.getLogger(__name__)


class FraudInvestigationAgent:
    """
    The main fraud investigation agent.
    Orchestrates the full investigation lifecycle for each case.
    """

    def __init__(self, graph: InMemoryGraph, llm_client=None):
        self.graph = graph
        self.evidence_gatherer = EvidenceGatherer(graph)
        self.pattern_detector = PatternDetector()
        self.policy_engine = PolicyEngine()
        self.llm_client = llm_client
        self.tool_calls_total = 0
        self.tokens_total = 0

    def investigate(self, trigger: CasePackEntry) -> CaseAnswer:
        """
        Run a full investigation for a single case.
        Returns the complete answer in the required format.
        """
        start_time = time.time()
        logger.info("=== Investigating %s ===", trigger.case_id)
        logger.info("Trigger: %s | Card: %s | Customer: %s",
                     trigger.trigger_type.value, trigger.card_id, trigger.customer_id)

        # Initialize investigation state
        state = InvestigationState(
            case_id=trigger.case_id,
            trigger=trigger,
        )

        # Step 1: Trigger & Initial Evidence Gathering
        logger.info("Step 1: Gathering initial evidence...")
        state = self.evidence_gatherer.gather_initial_evidence(state)
        state.steps_completed += 1

        # Step 2: Device & Connection Investigation
        logger.info("Step 2: Investigating device connections...")
        state = self.evidence_gatherer.gather_device_evidence(state)
        state.steps_completed += 1

        # Step 3: Velocity Analysis
        logger.info("Step 3: Analyzing transaction velocity...")
        state = self.evidence_gatherer.gather_velocity_evidence(state)
        state.steps_completed += 1

        # Step 4: Pattern Detection
        logger.info("Step 4: Detecting fraud patterns...")
        state = self.pattern_detector.detect_patterns(state)
        state.steps_completed += 1

        # Step 5: Find Similar Closed Cases (case memory)
        logger.info("Step 5: Searching case memory...")
        state = self.evidence_gatherer.find_similar_closed_cases(state)
        state.steps_completed += 1

        # Calculate initial fraud probability
        state.fraud_probability = self.pattern_detector.calculate_fraud_probability(state)
        logger.info("Initial fraud probability: %.2f | Pattern: %s",
                     state.fraud_probability, state.pattern.value)

        # Step 6: Determine if more evidence needed & initial actions
        logger.info("Step 6: Evaluating initial actions...")
        initial_nba = self.policy_engine.evaluate_initial_actions(state)

        # Determine if we should request more evidence
        evidence_requests = self._decide_evidence_requests(state)
        state.evidence_requests = evidence_requests

        # If evidence requested, simulate the response and re-evaluate
        if evidence_requests:
            logger.info("Step 6b: Processing evidence requests...")
            for req in evidence_requests:
                req.assumed_response = self._simulate_evidence_response(state, req)
                state.steps_completed += 1

            # Re-gather evidence and reassess after simulated responses
            state.fraud_probability = self.pattern_detector.calculate_fraud_probability(state)
            logger.info("Updated fraud probability after evidence requests: %.2f",
                         state.fraud_probability)

        # Step 7: Final actions
        logger.info("Step 7: Determining final actions...")
        final_nba = self.policy_engine.evaluate_final_actions(state, evidence_requests)

        # Evaluate SAR
        sar = self.policy_engine.evaluate_sar(state)

        # Determine stop reason
        should_stop, stop_reason = self.policy_engine.should_stop_investigation(state)
        if not stop_reason:
            stop_reason = "Investigation complete; all available evidence examined"

        # Determine final status
        case_status = self.policy_engine.determine_status(state)

        # Build the case
        affected_txn_ids = list(set(
            [t.transaction_id for t in state.card_transactions[-10:]]
            + [state.trigger.flagged_txn_id]
        ))
        exposure = sum(
            t.amount for t in state.card_transactions[-10:]
            if t.transaction_id in affected_txn_ids
        )

        investigation_case = InvestigationCase(
            status=case_status,
            verdict=state.verdict,
            fraud_probability=state.fraud_probability,
            pattern=state.pattern,
            pattern_description=state.pattern_description,
            affected_txn_ids=affected_txn_ids if state.verdict == Verdict.FRAUD else [],
            first_suspicious_txn_id=(
                affected_txn_ids[0] if affected_txn_ids and state.verdict == Verdict.FRAUD else ""
            ),
            connected_card_ids=state.connected_card_ids,
            connected_device_profiles=[
                n.get("device_id", "") for n in state.device_neighbors
            ],
            exposure_usd=exposure if state.verdict == Verdict.FRAUD else 0.0,
            evidence=state.evidence_collected,
            similar_prior_cases=[c.case_id for c in state.similar_closed_cases],
            summary=self._build_summary(state),
            written_to_graph=True,
            graph_case_id=f"CASE-{state.case_id}",
        )

        # Step 8: Write to graph (case memory)
        logger.info("Step 8: Writing case to graph...")
        self._write_case_to_graph(state, investigation_case)

        # Build final answer
        latency = time.time() - start_time

        answer = CaseAnswer(
            case_id=trigger.case_id,
            case=investigation_case,
            evidence_requests=evidence_requests,
            next_best_actions=final_nba,
            sar=sar,
            stop_reason=stop_reason,
            tool_calls=state.tool_calls,
            tokens=self.tokens_total,
            latency_s=round(latency, 1),
        )

        logger.info("=== %s Complete === Verdict: %s | Probability: %.2f | "
                     "Pattern: %s | Actions: %d | Took %.1fs",
                     trigger.case_id, state.verdict.value,
                     state.fraud_probability, state.pattern.value,
                     len(final_nba.final), latency)

        return answer

    def _decide_evidence_requests(self, state: InvestigationState) -> list[EvidenceRequest]:
        """Decide if additional evidence should be requested."""
        requests = []

        # Request customer validation if:
        # - Probability is moderate (0.30 - 0.70) and it's a customer report or risk score trigger
        # - Policy R1 says to verify before blocking
        prob = state.fraud_probability

        if state.trigger.trigger_type == "customer_report":
            # Customer report - assume they denied (the message says "I never made this")
            requests.append(EvidenceRequest(
                type="customer_validation",
                asked_after_step=state.steps_completed,
                assumed_response=(
                    f"Customer {state.trigger.customer_id} states they did not make "
                    f"this purchase and still has the card"
                ),
            ))
        elif 0.30 <= prob < 0.85:
            # Moderate risk - request verification
            requests.append(EvidenceRequest(
                type="customer_validation",
                asked_after_step=state.steps_completed,
                assumed_response=self._simulate_customer_response(state),
            ))

        return requests

    def _simulate_evidence_response(self, state: InvestigationState,
                                     request: EvidenceRequest) -> str:
        """Simulate a response to an evidence request."""
        if request.type == "customer_validation":
            return self._simulate_customer_response(state)
        elif request.type == "step_up_auth":
            return "Authentication completed successfully"
        elif request.type == "analyst_info":
            return "Analyst confirmed suspicious pattern"
        return ""

    def _simulate_customer_response(self, state: InvestigationState) -> str:
        """
        Simulate customer response based on investigation signals.
        Customer reports ("I never made this purchase") → denied
        High-risk signals → likely denied
        Low-risk patterns → likely confirmed
        """
        # Customer reports always deny
        if state.trigger.trigger_type == "customer_report":
            return (
                f"Customer {state.trigger.customer_id} states they did not make "
                f"this purchase and still has the card"
            )

        # High probability fraud → deny
        if state.fraud_probability >= 0.60:
            return (
                f"Customer {state.trigger.customer_id} states they did not make "
                f"this purchase"
            )

        # Low probability → confirm (recurring charge, etc.)
        if state.fraud_probability < 0.30:
            return "Customer confirms this is a legitimate purchase"

        # Default: assume denial for moderate risk
        return (
            f"Customer {state.trigger.customer_id} cannot recall this transaction "
            f"and requests further investigation"
        )

    def _build_summary(self, state: InvestigationState) -> str:
        """Build a 2-6 sentence summary for the case."""
        trigger = state.trigger
        flagged = state.flagged_txn

        parts = []

        # What happened
        if flagged:
            parts.append(
                f"Transaction {flagged.transaction_id} for ${flagged.amount:.2f} "
                f"({flagged.channel}) on card {trigger.card_id} was flagged "
                f"via {trigger.trigger_type.value}."
            )

        # Pattern found
        if state.pattern != FraudPattern.NONE:
            if state.pattern_description:
                parts.append(state.pattern_description)
            else:
                parts.append(f"Pattern detected: {state.pattern.value.replace('_', ' ')}.")

        # Key evidence
        key_evidence = [ev for ev in state.evidence_collected[:3]
                        if ev.source == SourceType.GRAPH]
        if key_evidence:
            parts.append(
                "Key evidence: " + "; ".join(ev.claim for ev in key_evidence[:2]) + "."
            )

        # Prior cases
        if state.similar_closed_cases:
            case_ids = [c.case_id for c in state.similar_closed_cases[:2]]
            parts.append(f"Similar prior cases: {', '.join(case_ids)}.")

        # Verdict
        parts.append(
            f"Verdict: {state.verdict.value} "
            f"(probability {state.fraud_probability:.2f})."
        )

        return " ".join(parts)

    def _write_case_to_graph(self, state: InvestigationState,
                              case: InvestigationCase):
        """Write the investigation case to the graph for case memory."""
        graph_case_id = case.graph_case_id

        # Write case vertex
        self.graph.write_investigation_case(graph_case_id, {
            "case_id": graph_case_id,
            "customer_id": state.trigger.customer_id,
            "card_id": state.trigger.card_id,
            "opened_at": state.trigger.opened_at,
            "status": case.status.value,
            "verdict": case.verdict.value,
            "fraud_probability": case.fraud_probability,
            "pattern": case.pattern.value,
            "exposure_usd": case.exposure_usd,
            "summary": case.summary,
        })

        # Link to transactions
        for txn_id in case.affected_txn_ids:
            self.graph.link_case_to_transaction(graph_case_id, txn_id)

        # Link to card
        self.graph.link_case_to_card(graph_case_id, state.trigger.card_id)

        # Link to devices
        for device_id in case.connected_device_profiles:
            if device_id:
                self.graph.link_case_to_device(graph_case_id, device_id)

        # Link to similar closed cases
        for closed_case_id in case.similar_prior_cases:
            self.graph.link_case_to_closed_case(
                graph_case_id, closed_case_id, 0.8
            )

        logger.info("Case %s written to graph as %s",
                     state.case_id, graph_case_id)

    def investigate_all(self, case_pack: list[CasePackEntry],
                         output_dir: str = "cases") -> list[CaseAnswer]:
        """Run investigation on all cases in the case pack."""
        answers = []
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        for i, trigger in enumerate(case_pack):
            logger.info("Processing case %d/%d: %s",
                         i + 1, len(case_pack), trigger.case_id)
            try:
                answer = self.investigate(trigger)
                answers.append(answer)

                # Write answer to file
                answer_path = output_path / f"{trigger.case_id}.json"
                with open(answer_path, "w") as f:
                    json.dump(answer.model_dump(), f, indent=2, default=str)

                logger.info("Answer written to %s", answer_path)

            except Exception as e:
                logger.error("Error investigating %s: %s", trigger.case_id, e)
                import traceback
                traceback.print_exc()

        # Print summary
        self._print_summary(answers)
        return answers

    def _print_summary(self, answers: list[CaseAnswer]):
        """Print investigation summary."""
        total = len(answers)
        fraud = sum(1 for a in answers if a.case.verdict == Verdict.FRAUD)
        legit = sum(1 for a in answers if a.case.verdict == Verdict.LEGITIMATE)
        uncertain = sum(1 for a in answers if a.case.verdict == Verdict.UNCERTAIN)
        sar_filed = sum(1 for a in answers if a.sar.file)
        total_tools = sum(a.tool_calls for a in answers)

        logger.info("=" * 60)
        logger.info("INVESTIGATION SUMMARY")
        logger.info("=" * 60)
        logger.info("Total cases: %d", total)
        logger.info("Fraud: %d | Legitimate: %d | Uncertain: %d", fraud, legit, uncertain)
        logger.info("SARs filed: %d", sar_filed)
        logger.info("Total tool calls: %d", total_tools)
        logger.info("=" * 60)
