"""
Hybrid Agent Orchestrator: combines rule-based policy enforcement with LLM reasoning.

Flow:
1. Rule-based: gather evidence from graph (fast, deterministic)
2. LLM-based: assess evidence, detect patterns, reason about uncertainty
3. Rule-based: enforce policy actions (R1-R10, approval routing)
4. LLM-based: generate explanations and SAR narratives
"""
from __future__ import annotations
import json
import logging
import time
from pathlib import Path
from typing import Optional

from ..graph.in_memory_graph import InMemoryGraph
from ..graph.data_loader import DataLoader
from ..evidence.gatherer import EvidenceGatherer
from ..evidence.pattern_detector import PatternDetector
from ..evidence.graphrag import GraphRAGSynthesizer
from ..policy.engine import PolicyEngine
from ..agent.llm_reasoner import LLMReasoner
from ..utils.models import (
    CasePackEntry, CaseAnswer, InvestigationCase, InvestigationState,
    CaseStatus, Verdict, FraudPattern, EvidenceRequest, SourceType,
    ActionType, ApprovalRoute, ActionRecommendation,
)

logger = logging.getLogger(__name__)


class FraudInvestigationAgent:
    """
    Hybrid fraud investigation agent.
    Uses graph traversal for evidence, LLM for reasoning, rules for policy.
    """

    def __init__(self, graph: InMemoryGraph, llm_provider: str = None,
                 llm_model: str = None):
        self.graph = graph
        self.evidence_gatherer = EvidenceGatherer(graph)
        self.pattern_detector = PatternDetector()
        self.policy_engine = PolicyEngine()
        self.graphrag = GraphRAGSynthesizer(graph)
        self.llm = LLMReasoner(provider=llm_provider, model=llm_model)

        if self.llm.is_llm_available:
            logger.info("Hybrid mode: LLM reasoning enabled (%s)", llm_provider)
        else:
            logger.info("Rule-based mode: LLM not available, using rules only")

    def investigate(self, trigger: CasePackEntry) -> CaseAnswer:
        """Run a full hybrid investigation for a single case."""
        start_time = time.time()
        logger.info("=== Investigating %s ===", trigger.case_id)

        state = InvestigationState(case_id=trigger.case_id, trigger=trigger)

        # ──────────────────────────────────────────────────────
        # STEP 1-3: Graph-based evidence gathering (deterministic)
        # ──────────────────────────────────────────────────────
        logger.info("Step 1: Gathering initial evidence from graph...")
        state = self.evidence_gatherer.gather_initial_evidence(state)
        state.steps_completed += 1

        logger.info("Step 2: Investigating device connections...")
        state = self.evidence_gatherer.gather_device_evidence(state)
        state.steps_completed += 1

        logger.info("Step 3: Analyzing velocity and patterns...")
        state = self.evidence_gatherer.gather_velocity_evidence(state)
        state = self.pattern_detector.detect_patterns(state)
        state.steps_completed += 1

        logger.info("Step 4: Retrieving case memory...")
        state = self.evidence_gatherer.find_similar_closed_cases(state)
        state.steps_completed += 1

        # ──────────────────────────────────────────────────────
        # STEP 5: GraphRAG context synthesis
        # ──────────────────────────────────────────────────────
        logger.info("Step 5: Building GraphRAG context...")
        graph_context = self.graphrag.build_investigation_context(state)
        policy_context = self.graphrag.build_policy_context(state)
        state.steps_completed += 1

        # ──────────────────────────────────────────────────────
        # STEP 6: LLM-based assessment (hybrid intelligence)
        # ──────────────────────────────────────────────────────
        logger.info("Step 6: LLM assessment of evidence...")
        assessment = self._llm_assess(state, graph_context)
        state.steps_completed += 1

        # ──────────────────────────────────────────────────────
        # STEP 7: Evidence requests and reassessment
        # ──────────────────────────────────────────────────────
        logger.info("Step 7: Determining evidence needs...")
        evidence_requests = self._decide_evidence_requests(state)
        state.evidence_requests = evidence_requests

        if evidence_requests:
            for req in evidence_requests:
                req.assumed_response = self._simulate_response(state, req)
                state.steps_completed += 1

            # Reassess after evidence request
            state.fraud_probability = self.pattern_detector.calculate_fraud_probability(state)
            if self.llm.is_llm_available:
                # Ask LLM to reassess with new evidence
                reassessment = self.llm.assess_evidence(state, graph_context)
                if reassessment.get("fraud_probability"):
                    state.fraud_probability = reassessment["fraud_probability"]
                if reassessment.get("verdict"):
                    state.verdict = Verdict(reassessment["verdict"])
                logger.info("LLM reassessment: prob=%.2f, verdict=%s",
                            state.fraud_probability, state.verdict.value)

        # ──────────────────────────────────────────────────────
        # STEP 8: Policy-based action determination
        # ──────────────────────────────────────────────────────
        logger.info("Step 8: Determining actions (policy engine)...")
        final_nba = self.policy_engine.evaluate_final_actions(state, evidence_requests)
        sar = self.policy_engine.evaluate_sar(state)

        # ──────────────────────────────────────────────────────
        # STEP 9: LLM-generated explanations
        # ──────────────────────────────────────────────────────
        logger.info("Step 9: Generating explanations...")
        summary = self._generate_summary(state, graph_context)

        # Enhance SAR narrative with LLM if available
        if sar.file and self.llm.is_llm_available:
            sar.narrative = self.llm.generate_sar_narrative(
                state, sar.total_amount_usd
            )

        # Determine stop reason and status
        should_stop, stop_reason = self.policy_engine.should_stop_investigation(state)
        if not stop_reason:
            stop_reason = "Investigation complete; all available evidence examined"
        case_status = self.policy_engine.determine_status(state)

        # ──────────────────────────────────────────────────────
        # STEP 10: Build case and write to graph
        # ──────────────────────────────────────────────────────
        logger.info("Step 10: Writing case to graph...")
        affected_txn_ids = self._get_affected_txn_ids(state)
        exposure = sum(t.amount for t in state.card_transactions[-10:]
                       if t.transaction_id in affected_txn_ids)

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
            summary=summary,
            written_to_graph=True,
            graph_case_id=f"CASE-{state.case_id}",
        )

        self._write_case_to_graph(state, investigation_case)

        latency = time.time() - start_time

        answer = CaseAnswer(
            case_id=trigger.case_id,
            case=investigation_case,
            evidence_requests=evidence_requests,
            next_best_actions=final_nba,
            sar=sar,
            stop_reason=stop_reason,
            tool_calls=state.tool_calls,
            tokens=self.llm.total_tokens,
            latency_s=round(latency, 1),
        )

        logger.info("=== %s Complete === Verdict: %s | Prob: %.2f | Pattern: %s | "
                     "Actions: %d | LLM tokens: %d | %.1fs",
                     trigger.case_id, state.verdict.value, state.fraud_probability,
                     state.pattern.value, len(final_nba.final), self.llm.total_tokens, latency)

        return answer

    def _llm_assess(self, state: InvestigationState,
                     graph_context: str) -> dict:
        """Use LLM for evidence assessment, fall back to rules."""
        # Rule-based initial assessment
        state.fraud_probability = self.pattern_detector.calculate_fraud_probability(state)

        if not self.llm.is_llm_available:
            return {
                "fraud_probability": state.fraud_probability,
                "verdict": state.verdict.value,
                "pattern": state.pattern.value,
            }

        # Ask LLM to assess
        assessment = self.llm.assess_evidence(state, graph_context)

        # Update state with LLM assessment
        if assessment.get("fraud_probability") is not None:
            state.fraud_probability = float(assessment["fraud_probability"])
        if assessment.get("verdict"):
            try:
                state.verdict = Verdict(assessment["verdict"])
            except ValueError:
                pass
        if assessment.get("pattern"):
            try:
                state.pattern = FraudPattern(assessment["pattern"])
            except ValueError:
                pass
        if assessment.get("pattern_description"):
            state.pattern_description = assessment["pattern_description"]

        # Add LLM reasoning as evidence
        if assessment.get("reasoning"):
            state.evidence_collected.append(
                self._make_evidence(
                    f"LLM analysis: {assessment['reasoning']}",
                    SourceType.DOCUMENT,
                    "llm:assess_evidence",
                )
            )

        logger.info("LLM assessment: prob=%.2f, verdict=%s, pattern=%s",
                     state.fraud_probability, state.verdict.value, state.pattern.value)

        return assessment

    def _decide_evidence_requests(self, state: InvestigationState) -> list[EvidenceRequest]:
        """Decide if more evidence is needed."""
        requests = []

        if state.trigger.trigger_type == "customer_report":
            requests.append(EvidenceRequest(
                type="customer_validation",
                asked_after_step=state.steps_completed,
                assumed_response=(
                    f"Customer {state.trigger.customer_id} states they did not make "
                    f"this purchase and still has the card"
                ),
            ))
        elif 0.30 <= state.fraud_probability < 0.85:
            requests.append(EvidenceRequest(
                type="customer_validation",
                asked_after_step=state.steps_completed,
                assumed_response=self._simulate_customer_response(state),
            ))

        return requests

    def _simulate_response(self, state: InvestigationState,
                            request: EvidenceRequest) -> str:
        if request.type == "customer_validation":
            return self._simulate_customer_response(state)
        elif request.type == "step_up_auth":
            return "Authentication completed successfully"
        return ""

    def _simulate_customer_response(self, state: InvestigationState) -> str:
        if state.trigger.trigger_type == "customer_report":
            return (
                f"Customer {state.trigger.customer_id} states they did not make "
                f"this purchase and still has the card"
            )
        if state.fraud_probability >= 0.60:
            return f"Customer {state.trigger.customer_id} states they did not make this purchase"
        if state.fraud_probability < 0.30:
            return "Customer confirms this is a legitimate purchase"
        return f"Customer {state.trigger.customer_id} cannot recall this transaction"

    def _generate_summary(self, state: InvestigationState,
                           graph_context: str) -> str:
        """Generate case summary, using LLM if available."""
        if self.llm.is_llm_available:
            llm_summary = self.llm.generate_explanation(state)
            if llm_summary:
                return llm_summary

        # Fallback: template-based
        parts = []
        if state.flagged_txn:
            parts.append(
                f"Transaction {state.flagged_txn.transaction_id} for "
                f"${state.flagged_txn.amount:.2f} ({state.flagged_txn.channel}) "
                f"on card {state.trigger.card_id} was flagged via "
                f"{state.trigger.trigger_type.value}."
            )
        if state.pattern != FraudPattern.NONE:
            if state.pattern_description:
                parts.append(state.pattern_description)
            else:
                parts.append(f"Pattern: {state.pattern.value.replace('_', ' ')}.")
        key_ev = [ev for ev in state.evidence_collected[:2] if ev.source == SourceType.GRAPH]
        if key_ev:
            parts.append("Evidence: " + "; ".join(e.claim for e in key_ev) + ".")
        if state.similar_closed_cases:
            parts.append(f"Similar cases: {', '.join(c.case_id for c in state.similar_closed_cases[:2])}.")
        parts.append(f"Verdict: {state.verdict.value} ({state.fraud_probability:.2f}).")
        return " ".join(parts)

    def _get_affected_txn_ids(self, state: InvestigationState) -> list[str]:
        ids = set()
        if state.trigger.flagged_txn_id:
            ids.add(state.trigger.flagged_txn_id)
        for t in state.card_transactions[-10:]:
            ids.add(t.transaction_id)
        for t in state.connected_transactions:
            ids.add(t.transaction_id)
        return list(ids)

    def _write_case_to_graph(self, state: InvestigationState,
                              case: InvestigationCase):
        self.graph.write_investigation_case(case.graph_case_id, {
            "case_id": case.graph_case_id,
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
        for txn_id in case.affected_txn_ids:
            self.graph.link_case_to_transaction(case.graph_case_id, txn_id)
        self.graph.link_case_to_card(case.graph_case_id, state.trigger.card_id)
        for device_id in case.connected_device_profiles:
            if device_id:
                self.graph.link_case_to_device(case.graph_case_id, device_id)
        for closed_id in case.similar_prior_cases:
            self.graph.link_case_to_closed_case(case.graph_case_id, closed_id, 0.8)

    def _make_evidence(self, claim: str, source: SourceType,
                        ref: str) -> Evidence:
        return Evidence(claim=claim, source=source, ref=ref, entity_ids=[])

    def investigate_all(self, case_pack: list[CasePackEntry],
                         output_dir: str = "cases") -> list[CaseAnswer]:
        answers = []
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        for i, trigger in enumerate(case_pack):
            logger.info("Processing %d/%d: %s", i + 1, len(case_pack), trigger.case_id)
            try:
                answer = self.investigate(trigger)
                answers.append(answer)
                with open(output_path / f"{trigger.case_id}.json", "w") as f:
                    json.dump(answer.model_dump(), f, indent=2, default=str)
            except Exception as e:
                logger.error("Error on %s: %s", trigger.case_id, e)
                import traceback
                traceback.print_exc()

        self._print_summary(answers)
        return answers

    def _print_summary(self, answers: list[CaseAnswer]):
        fraud = sum(1 for a in answers if a.case.verdict == Verdict.FRAUD)
        legit = sum(1 for a in answers if a.case.verdict == Verdict.LEGITIMATE)
        uncertain = sum(1 for a in answers if a.case.verdict == Verdict.UNCERTAIN)
        sars = sum(1 for a in answers if a.sar.file)
        total_tokens = sum(a.tokens for a in answers)

        logger.info("=" * 60)
        logger.info("INVESTIGATION SUMMARY")
        logger.info("=" * 60)
        logger.info("Total: %d | Fraud: %d | Legit: %d | Uncertain: %d",
                     len(answers), fraud, legit, uncertain)
        logger.info("SARs filed: %d | Total LLM tokens: %d", sars, total_tokens)
        logger.info("=" * 60)
