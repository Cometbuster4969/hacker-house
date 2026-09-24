"""
LLM Reasoner: uses an LLM to reason about fraud evidence, detect patterns,
assess risk, and generate explanations. This is the "AI" in the hybrid approach.
"""
from __future__ import annotations
import json
import logging
import os
from typing import Optional

from ..utils.models import (
    InvestigationState, Verdict, FraudPattern, Evidence,
    ActionRecommendation, ActionType, ApprovalRoute,
    NextBestActions, SuspiciousActivityReport,
)

logger = logging.getLogger(__name__)


# System prompt that makes the LLM a fraud analyst
SYSTEM_PROMPT = """You are a senior fraud investigation analyst at a major bank. 
You analyze transaction data, device signals, customer history, and prior cases 
to determine whether activity is fraudulent, what type of fraud it is, and what 
actions the bank should take.

You must be precise and calibrated:
- Half of all alerts are legitimate. Do not assume fraud.
- A risk score is a reason to look, never a verdict.
- Some fraud scores near zero are actual fraud.
- One unusual transaction is not proof. People buy new phones, take trips, make large purchases.
- Customer reports ("I never made this purchase") are strong signals but not proof — verify.
- Shared devices across multiple cards are strong indicators of organized fraud.

You must cite evidence for every conclusion. You must follow the bank's policy rules exactly.

Respond in JSON format as specified in each prompt."""


class LLMReasoner:
    """
    Uses an LLM for fraud investigation reasoning.
    Provides structured prompts for each investigation step and parses responses.
    """

    def __init__(self, provider: str = None, model: str = None):
        self.provider = provider or os.getenv("LLM_PROVIDER", "mock")
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o")
        self._client = None
        self._total_tokens = 0

        if self.provider == "openai":
            try:
                import openai
                self._client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
                logger.info("Using OpenAI %s", self.model)
            except Exception as e:
                logger.warning("OpenAI init failed: %s, falling back to mock", e)
                self.provider = "mock"
        elif self.provider == "anthropic":
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
                logger.info("Using Anthropic %s", self.model)
            except Exception as e:
                logger.warning("Anthropic init failed: %s, falling back to mock", e)
                self.provider = "mock"

    @property
    def is_llm_available(self) -> bool:
        return self.provider != "mock" and self._client is not None

    def assess_evidence(self, state: InvestigationState,
                        graph_context: str) -> dict:
        """
        Ask the LLM to assess the evidence and determine:
        - fraud_probability (0-1)
        - verdict (fraud/legitimate/uncertain)
        - pattern (card_testing/cnp_fraud/etc.)
        - pattern_description
        - reasoning
        """
        if not self.is_llm_available:
            return self._fallback_assessment(state)

        prompt = f"""Analyze this fraud investigation and provide your assessment.

## Investigation Context
{graph_context}

## Evidence Collected
{self._format_evidence(state.evidence_collected)}

## Customer Report
{state.trigger.trigger_text}

## Similar Prior Cases
{self._format_prior_cases(state.similar_closed_cases)}

Respond in this exact JSON format:
{{
    "fraud_probability": <float 0-1>,
    "verdict": "<fraud|legitimate|uncertain>",
    "pattern": "<card_testing|card_not_present_fraud|card_not_present_new_device|out_of_region_use|account_takeover|undocumented|none>",
    "pattern_description": "<2-3 sentences if undocumented, empty string otherwise>",
    "confidence": "<high|medium|low>",
    "key_evidence": ["<list of the strongest evidence claims>"],
    "reasoning": "<3-5 sentences explaining your assessment>"
}}"""

        response = self._call_llm(prompt)
        return self._parse_json_response(response, self._fallback_assessment(state))

    def determine_actions(self, state: InvestigationState,
                          policy_context: str) -> dict:
        """
        Ask the LLM to recommend next-best-actions based on evidence and policy.
        """
        if not self.is_llm_available:
            return self._fallback_actions(state)

        prompt = f"""Based on the investigation findings and bank policy, recommend actions.

## Investigation Findings
- Case ID: {state.case_id}
- Verdict: {state.verdict.value}
- Fraud Probability: {state.fraud_probability:.2f}
- Pattern: {state.pattern.value}
- Trigger: {state.trigger.trigger_type.value} — {state.trigger.trigger_text}

## Evidence
{self._format_evidence(state.evidence_collected)}

## Exposure
Total amount at risk: ${sum(t.amount for t in state.card_transactions[-10:]):.2f}

## Connected Entities
- Connected cards: {', '.join(state.connected_card_ids) or 'None'}
- Shared devices: {len(state.device_neighbors)}

## Policy Rules
{policy_context}

Respond in this exact JSON format:
{{
    "initial_actions": [
        {{"action": "<ACTION_NAME>", "route": "<auto|L1|L2>", "reason": "<cite policy rule>"}}
    ],
    "final_actions": [
        {{"action": "<ACTION_NAME>", "route": "<auto|L1|L2>", "reason": "<cite policy rule>"}}
    ],
    "what_changed": "<1-2 sentences on what changed between initial and final>",
    "sar_required": <true|false>,
    "sar_reason": "<why or why not, cite policy>"
}}

Valid actions: ALLOW_TRANSACTION, DECLINE_TRANSACTION, MONITOR_CARD, MONITOR_CONNECTED_CARDS, 
WARN_CUSTOMER, VERIFY_WITH_CUSTOMER, STEP_UP_AUTH, BLOCK_CARD, BLOCK_ALL_CARDS, 
GENERATE_REPORT, CREATE_CASE, FILE_REPORT, ESCALATE_TO_ANALYST, CLOSE_NO_FRAUD"""

        response = self._call_llm(prompt)
        return self._parse_json_response(response, self._fallback_actions(state))

    def generate_sar_narrative(self, state: InvestigationState,
                                exposure: float) -> str:
        """Ask the LLM to write a SAR narrative."""
        if not self.is_llm_available:
            return self._fallback_sar_narrative(state, exposure)

        prompt = f"""Write a Suspicious Activity Report (SAR) narrative for this case.
The narrative must stand on its own: who, what, when, where, how, and why it is suspicious.
This is what a regulator reads. Be factual, specific, and complete.

## Case Details
- Customer: {state.trigger.customer_id}
- Card: {state.trigger.card_id}
- Pattern: {state.pattern.value}
- Verdict: {state.verdict.value}
- Fraud Probability: {state.fraud_probability:.2f}

## Evidence
{self._format_evidence(state.evidence_collected)}

## Connected Entities
- Connected cards: {', '.join(state.connected_card_ids) or 'None'}
- Shared devices: {[n.get('device_id', '') for n in state.device_neighbors] or 'None'}

## Prior Cases
{self._format_prior_cases(state.similar_closed_cases)}

## Total Exposure
${exposure:.2f}

Write 6-12 sentences. Include specific transaction IDs, amounts, dates, device profiles, and any connections to other cards or cases. Do not speculate — only state what the evidence shows."""

        response = self._call_llm(prompt)
        return response if response else self._fallback_sar_narrative(state, exposure)

    def generate_explanation(self, state: InvestigationState) -> str:
        """Ask the LLM to generate a clear explanation for the investigation."""
        if not self.is_llm_available:
            return self._fallback_explanation(state)

        prompt = f"""Write a clear 2-6 sentence summary of this fraud investigation that an analyst could read.
Include: what was flagged, what you found, what pattern it matches, and what you recommend.

## Findings
- Verdict: {state.verdict.value}
- Probability: {state.fraud_probability:.2f}
- Pattern: {state.pattern.value}
- {state.pattern_description if state.pattern_description else ''}

## Key Evidence
{self._format_evidence(state.evidence_collected[:5])}

## Prior Cases
{self._format_prior_cases(state.similar_closed_cases[:2])}

Write a concise, professional summary."""

        response = self._call_llm(prompt)
        return response if response else self._fallback_explanation(state)

    def detect_undocumented_pattern(self, state: InvestigationState,
                                     graph_context: str) -> Optional[str]:
        """Ask the LLM if there's an undocumented fraud pattern."""
        if not self.is_llm_available:
            return None

        # Only ask if no known pattern detected
        if state.pattern != FraudPattern.NONE:
            return None

        prompt = f"""Look at this investigation evidence and determine if there's a 
fraud pattern that doesn't match any of the 5 known patterns:

Known patterns:
1. Card testing: small authorizations then larger purchase
2. Card-not-present fraud: unusual online activity
3. CNP new device: online fraud from a device marked New
4. Out-of-region use: card-present in unfamiliar billing region
5. Account takeover: mixed-channel with credential anomalies

## Evidence
{self._format_evidence(state.evidence_collected)}

## Graph Context
{graph_context}

If you see coordinated abuse, a fraud ring, or a pattern not listed above, describe it in 2-3 sentences.
If the activity looks like one of the known patterns or is not clearly fraud, respond with "none".

Respond in JSON: {{"has_undocumented": <true|false>, "description": "<pattern description or empty string>"}}"""

        response = self._call_llm(prompt)
        parsed = self._parse_json_response(response, None)
        if parsed and parsed.get("has_undocumented") and parsed.get("description"):
            return parsed["description"]
        return None

    def _call_llm(self, prompt: str) -> Optional[str]:
        """Call the LLM with a prompt and return the response text."""
        if not self._client:
            return None

        try:
            if self.provider == "openai":
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.1,  # Low temperature for consistent analysis
                    max_tokens=2000,
                    response_format={"type": "json_object"},
                )
                self._total_tokens += response.usage.total_tokens
                return response.choices[0].message.content

            elif self.provider == "anthropic":
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=2000,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                )
                self._total_tokens += response.usage.input_tokens + response.usage.output_tokens
                return response.content[0].text

        except Exception as e:
            logger.error("LLM call failed: %s", e)
            return None

    def _parse_json_response(self, response: Optional[str],
                              fallback: dict) -> dict:
        """Parse JSON from LLM response, with fallback."""
        if not response:
            return fallback or {}
        try:
            # Handle markdown code blocks
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0]
            elif "```" in response:
                response = response.split("```")[1].split("```")[0]
            return json.loads(response.strip())
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM JSON response")
            return fallback or {}

    def _format_evidence(self, evidence: list[Evidence]) -> str:
        """Format evidence list for the LLM prompt."""
        if not evidence:
            return "No evidence collected yet."
        lines = []
        for i, ev in enumerate(evidence, 1):
            lines.append(f"{i}. [{ev.source.value}] {ev.claim}")
            if ev.ref:
                lines.append(f"   Source: {ev.ref}")
        return "\n".join(lines)

    def _format_prior_cases(self, cases) -> str:
        """Format prior cases for the LLM prompt."""
        if not cases:
            return "No similar prior cases found."
        lines = []
        for case in cases[:5]:
            lines.append(
                f"- {case.case_id}: {case.outcome} ({case.pattern}), "
                f"${case.exposure_usd:.2f} exposure. "
                f"Notes: {case.analyst_notes[:150] if case.analyst_notes else 'none'}"
            )
        return "\n".join(lines)

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    # --- Fallback methods (rule-based, used when LLM is not available) ---

    def _fallback_assessment(self, state: InvestigationState) -> dict:
        """Rule-based fallback when LLM is not available."""
        return {
            "fraud_probability": state.fraud_probability,
            "verdict": state.verdict.value,
            "pattern": state.pattern.value,
            "pattern_description": state.pattern_description,
            "confidence": "medium",
            "key_evidence": [e.claim for e in state.evidence_collected[:3]],
            "reasoning": "Rule-based assessment (LLM not available)",
        }

    def _fallback_actions(self, state: InvestigationState) -> dict:
        """Rule-based fallback for actions."""
        return {
            "initial_actions": [],
            "final_actions": [],
            "what_changed": "nothing",
            "sar_required": False,
            "sar_reason": "LLM not available for assessment",
        }

    def _fallback_sar_narrative(self, state: InvestigationState,
                                 exposure: float) -> str:
        """Template-based SAR narrative fallback."""
        trigger = state.trigger
        flagged = state.flagged_txn
        when = flagged.timestamp[:10] if flagged and flagged.timestamp else trigger.opened_at[:10]
        return (
            f"On {when}, customer {trigger.customer_id}, card {trigger.card_id} "
            f"was flagged for {state.pattern.value.replace('_', ' ')}. "
            f"Total exposure: ${exposure:.2f}. "
            f"Evidence: {'; '.join(e.claim for e in state.evidence_collected[:3])}."
        )

    def _fallback_explanation(self, state: InvestigationState) -> str:
        """Template-based explanation fallback."""
        parts = []
        if state.flagged_txn:
            parts.append(
                f"Transaction {state.flagged_txn.transaction_id} for "
                f"${state.flagged_txn.amount:.2f} was flagged via "
                f"{state.trigger.trigger_type.value}."
            )
        if state.pattern != FraudPattern.NONE:
            parts.append(
                f"Pattern detected: {state.pattern.value.replace('_', ' ')}."
            )
        parts.append(f"Verdict: {state.verdict.value} (probability {state.fraud_probability:.2f}).")
        return " ".join(parts)
