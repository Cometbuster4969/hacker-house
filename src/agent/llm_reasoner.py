"""
LLM Reasoner: uses an LLM to reason about fraud evidence, detect patterns,
assess risk, and generate explanations.

Supports:
- OpenAI (direct) — no rate limiting (used by evaluators)
- Anthropic (direct) — no rate limiting (used by evaluators)
- OpenRouter (free tier) — rate limited: 20 req/min, 50 req/day
- Mock (rule-based fallback) — no LLM, rules only
"""
from __future__ import annotations
import json
import logging
import os
import time
import threading
from collections import deque
from typing import Optional

from ..utils.models import (
    InvestigationState, Verdict, FraudPattern, Evidence,
    ActionRecommendation, ActionType, ApprovalRoute,
    NextBestActions, SuspiciousActivityReport,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Rate limiter for OpenRouter free tier
# ──────────────────────────────────────────────────────────────

class OpenRouterRateLimiter:
    """
    Rate limiter for OpenRouter free tier.
    
    Limits:
    - 20 requests per minute (hard cap, always)
    - 50 requests per day (no credits) or 1000/day ($10+ lifetime credits)
    
    When limit is hit, sleeps until the window resets.
    """

    def __init__(self, requests_per_minute: int = 18,  # 18 not 20, leave headroom
                 requests_per_day: int = 48):          # 48 not 50, leave headroom
        self.rpm = requests_per_minute
        self.rpd = requests_per_day
        self._minute_window: deque = deque()  # timestamps of requests in last 60s
        self._day_count = 0
        self._day_start = time.time()
        self._lock = threading.Lock()

    def wait_if_needed(self):
        """Block until a request slot is available."""
        with self._lock:
            now = time.time()

            # Reset daily counter at midnight (or every 24h)
            if now - self._day_start > 86400:
                self._day_count = 0
                self._day_start = now
                logger.info("Rate limiter: daily counter reset")

            # Check daily limit
            if self._day_count >= self.rpd:
                wait_time = 86400 - (now - self._day_start)
                logger.warning(
                    "Rate limiter: daily limit reached (%d/%d). "
                    "Waiting %.0f seconds until reset. "
                    "Tip: buy $10 OpenRouter credits to raise limit to 1000/day.",
                    self._day_count, self.rpd, wait_time
                )
                # Don't actually sleep for hours — just raise and let caller handle
                raise RateLimitExceeded(
                    f"Daily limit {self.rpd} reached. "
                    f"Resets in {wait_time/3600:.1f} hours. "
                    f"Buy $10 OpenRouter credits for 1000/day limit."
                )

            # Clean old entries from minute window
            cutoff = now - 60
            while self._minute_window and self._minute_window[0] < cutoff:
                self._minute_window.popleft()

            # Check per-minute limit
            if len(self._minute_window) >= self.rpm:
                sleep_time = self._minute_window[0] + 60 - now + 0.5
                if sleep_time > 0:
                    logger.info("Rate limiter: sleeping %.1fs for RPM limit", sleep_time)
                    time.sleep(sleep_time)

            # Record this request
            self._minute_window.append(time.time())
            self._day_count += 1

    @property
    def requests_remaining_today(self) -> int:
        return max(0, self.rpd - self._day_count)


class RateLimitExceeded(Exception):
    pass


# ──────────────────────────────────────────────────────────────
# OpenRouter model recommendations (free tier)
# ──────────────────────────────────────────────────────────────

# Best free models for fraud analysis (ordered by quality)
OPENROUTER_FREE_MODELS = {
    # Tier 1: Best reasoning for structured analysis
    "llama-3.3-70b": "meta-llama/llama-3.3-70b-instruct:free",
    "nemotron-120b": "nvidia/nemotron-3-super-120b-a12b:free",
    "qwen3-80b": "qwen/qwen3-next-80b-a3b-instruct:free",

    # Tier 2: Good balance of speed and quality
    "llama-4-maverick": "meta-llama/llama-4-maverick:free",
    "gemma-4-31b": "google/gemma-4-31b-it:free",

    # Tier 3: Fast, lower quality but reliable
    "gpt-oss-120b": "openai/gpt-oss-120b:free",
    "gpt-oss-20b": "openai/gpt-oss-20b:free",
}

# Default recommendation
DEFAULT_FREE_MODEL = "meta-llama/llama-3.3-70b-instruct:free"

# Model rotation order (used when primary model hits rate limits)
MODEL_ROTATION = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "meta-llama/llama-4-maverick:free",
    "google/gemma-4-31b-it:free",
]


# ──────────────────────────────────────────────────────────────
# System prompt
# ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a senior fraud investigation analyst at a major bank.
You analyze transaction data, device signals, customer history, and prior cases
to determine whether activity is fraudulent, what type of fraud it is, and what
actions the bank should take.

CRITICAL RULES:
- Half of all alerts are legitimate. Do NOT assume fraud.
- A risk score is a reason to look, never a verdict. Some fraud scores near zero.
- One unusual transaction is not proof. People buy new phones, take trips.
- Customer reports ("I never made this") are strong signals but verify.
- Shared devices across multiple cards = strong organized fraud indicator.
- Cite evidence for every conclusion.
- Follow the bank's policy rules exactly.
- Always respond in valid JSON format as specified."""


# ──────────────────────────────────────────────────────────────
# LLM Reasoner
# ──────────────────────────────────────────────────────────────

class LLMReasoner:
    """
    Hybrid LLM reasoner for fraud investigation.
    
    Provider behavior:
    - openai: Direct API, no rate limiting
    - anthropic: Direct API, no rate limiting
    - openrouter: Via OpenRouter, rate limited for free tier
    - mock: Rule-based fallback, no LLM
    """

    def __init__(self, provider: str = None, model: str = None):
        self.provider = provider or os.getenv("LLM_PROVIDER", "mock")
        self.model = model or os.getenv("LLM_MODEL", "")
        self._client = None
        self._total_tokens = 0
        self._rate_limiter = None
        self._rotation_index = 0

        # ── OpenRouter setup ──
        if self.provider == "openrouter":
            self._setup_openrouter()
        # ── Direct OpenAI ──
        elif self.provider == "openai":
            self._setup_openai()
        # ── Direct Anthropic ──
        elif self.provider == "anthropic":
            self._setup_anthropic()
        else:
            logger.info("LLM provider: mock (rule-based only)")

    def _setup_openrouter(self):
        """Setup OpenRouter client with rate limiting."""
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            logger.warning("OPENROUTER_API_KEY not set, falling back to mock")
            self.provider = "mock"
            return

        try:
            import openai

            # Resolve model name
            if not self.model or self.model == "gpt-4o":
                self.model = DEFAULT_FREE_MODEL
            elif self.model in OPENROUTER_FREE_MODELS:
                self.model = OPENROUTER_FREE_MODELS[self.model]
            # If it doesn't end with :free and isn't a known paid model, assume free
            elif ":free" not in self.model and "/" in self.model:
                self.model = self.model  # Use as-is (user specified full ID)

            self._client = openai.OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=api_key,
            )

            # Rate limiter ONLY for free tier models
            if ":free" in self.model:
                self._rate_limiter = OpenRouterRateLimiter(
                    requests_per_minute=18,
                    requests_per_day=48,
                )
                logger.info(
                    "OpenRouter FREE tier: %s (rate limited: 18 req/min, 48 req/day). "
                    "Buy $10 credits for 1000/day limit.",
                    self.model
                )
            else:
                logger.info("OpenRouter PAID model: %s (no rate limiting)", self.model)

        except Exception as e:
            logger.warning("OpenRouter setup failed: %s, falling back to mock", e)
            self.provider = "mock"

    def _setup_openai(self):
        """Setup direct OpenAI client — NO rate limiting."""
        try:
            import openai
            self._client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            self.model = self.model or "gpt-4o"
            logger.info("OpenAI direct: %s (no rate limiting)", self.model)
        except Exception as e:
            logger.warning("OpenAI setup failed: %s", e)
            self.provider = "mock"

    def _setup_anthropic(self):
        """Setup direct Anthropic client — NO rate limiting."""
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            self.model = self.model or "claude-sonnet-4-20250514"
            logger.info("Anthropic direct: %s (no rate limiting)", self.model)
        except Exception as e:
            logger.warning("Anthropic setup failed: %s", e)
            self.provider = "mock"

    @property
    def is_llm_available(self) -> bool:
        return self.provider != "mock" and self._client is not None

    # ──────────────────────────────────────────────────────
    # Core LLM methods
    # ──────────────────────────────────────────────────────

    def assess_evidence(self, state: InvestigationState,
                        graph_context: str) -> dict:
        """Assess evidence and determine fraud probability, verdict, pattern."""
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
    "key_evidence": ["<list of strongest evidence claims>"],
    "reasoning": "<3-5 sentences explaining your assessment>"
}}"""

        response = self._call_llm(prompt)
        return self._parse_json_response(response, self._fallback_assessment(state))

    def determine_actions(self, state: InvestigationState,
                          policy_context: str) -> dict:
        """Recommend next-best-actions based on evidence and policy."""
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
    "what_changed": "<1-2 sentences>",
    "sar_required": <true|false>,
    "sar_reason": "<why or why not>"
}}

Valid actions: ALLOW_TRANSACTION, DECLINE_TRANSACTION, MONITOR_CARD, MONITOR_CONNECTED_CARDS, 
WARN_CUSTOMER, VERIFY_WITH_CUSTOMER, STEP_UP_AUTH, BLOCK_CARD, BLOCK_ALL_CARDS, 
GENERATE_REPORT, CREATE_CASE, FILE_REPORT, ESCALATE_TO_ANALYST, CLOSE_NO_FRAUD"""

        response = self._call_llm(prompt)
        return self._parse_json_response(response, self._fallback_actions(state))

    def generate_sar_narrative(self, state: InvestigationState,
                                exposure: float) -> str:
        """Write a SAR narrative for the regulator."""
        if not self.is_llm_available:
            return self._fallback_sar_narrative(state, exposure)

        prompt = f"""Write a Suspicious Activity Report (SAR) narrative. 
Must stand on its own: who, what, when, where, how, and why suspicious.
This is what a regulator reads. Be factual, specific, complete.

## Case Details
- Customer: {state.trigger.customer_id}
- Card: {state.trigger.card_id}
- Pattern: {state.pattern.value}
- Verdict: {state.verdict.value}

## Evidence
{self._format_evidence(state.evidence_collected)}

## Connected Entities
- Cards: {', '.join(state.connected_card_ids) or 'None'}
- Devices: {[n.get('device_id', '') for n in state.device_neighbors] or 'None'}

## Total Exposure: ${exposure:.2f}

Write 6-12 factual sentences. Include transaction IDs, amounts, dates, device profiles.
Do not speculate. Only state what evidence shows."""

        response = self._call_llm(prompt)
        return response if response else self._fallback_sar_narrative(state, exposure)

    def generate_explanation(self, state: InvestigationState) -> str:
        """Generate a clear case summary."""
        if not self.is_llm_available:
            return self._fallback_explanation(state)

        prompt = f"""Write a clear 2-6 sentence fraud investigation summary for an analyst.
Include: what was flagged, what you found, what pattern, what you recommend.

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
        """Ask LLM if there's an undocumented fraud pattern."""
        if not self.is_llm_available or state.pattern != FraudPattern.NONE:
            return None

        prompt = f"""Look at this evidence. Is there a fraud pattern NOT matching these 5?

Known: card_testing, card_not_present_fraud, card_not_present_new_device, 
       out_of_region_use, account_takeover

## Evidence
{self._format_evidence(state.evidence_collected)}

## Graph Context
{graph_context}

If you see coordinated abuse or a novel pattern, describe it in 2-3 sentences.
If it matches a known pattern or isn't clearly fraud, say "none".

JSON: {{"has_undocumented": <true|false>, "description": "<description or empty>"}}"""

        response = self._call_llm(prompt)
        parsed = self._parse_json_response(response, None)
        if parsed and parsed.get("has_undocumented") and parsed.get("description"):
            return parsed["description"]
        return None

    # ──────────────────────────────────────────────────────
    # LLM call with rate limiting and model rotation
    # ──────────────────────────────────────────────────────

    def _call_llm(self, prompt: str) -> Optional[str]:
        """Call the LLM with rate limiting for OpenRouter free tier."""
        if not self._client:
            return None

        # Apply rate limiting for OpenRouter free tier
        if self._rate_limiter:
            try:
                self._rate_limiter.wait_if_needed()
            except RateLimitExceeded as e:
                logger.warning("Rate limit: %s", e)
                # Try rotating to next free model
                new_model = self._rotate_model()
                if new_model:
                    logger.info("Rotating to model: %s", new_model)
                    self.model = new_model
                    try:
                        self._rate_limiter.wait_if_needed()
                    except RateLimitExceeded:
                        return None
                else:
                    return None

        try:
            if self.provider in ("openai", "openrouter"):
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.1,
                    max_tokens=2000,
                    # Only request JSON for OpenAI direct (not all OpenRouter models support it)
                    **({"response_format": {"type": "json_object"}} if self.provider == "openai" else {}),
                )
                self._total_tokens += response.usage.total_tokens if response.usage else 0
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
            error_str = str(e).lower()
            # Handle rate limit errors from OpenRouter
            if "429" in error_str or "rate" in error_str or "limit" in error_str:
                logger.warning("Rate limited by provider: %s", e)
                if self._rate_limiter:
                    new_model = self._rotate_model()
                    if new_model:
                        logger.info("Rotating to: %s", new_model)
                        self.model = new_model
                        return self._call_llm(prompt)  # Retry with new model
                return None

            logger.error("LLM call failed: %s", e)
            return None

    def _rotate_model(self) -> Optional[str]:
        """Rotate to next free model in the rotation list."""
        self._rotation_index += 1
        if self._rotation_index < len(MODEL_ROTATION):
            return MODEL_ROTATION[self._rotation_index]
        self._rotation_index = 0
        return None

    def _parse_json_response(self, response: Optional[str],
                              fallback: dict) -> dict:
        """Parse JSON from LLM response."""
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
            logger.warning("Failed to parse LLM JSON")
            return fallback or {}

    def _format_evidence(self, evidence: list[Evidence]) -> str:
        if not evidence:
            return "No evidence collected yet."
        return "\n".join(
            f"{i}. [{ev.source.value}] {ev.claim}" + (f"\n   Source: {ev.ref}" if ev.ref else "")
            for i, ev in enumerate(evidence, 1)
        )

    def _format_prior_cases(self, cases) -> str:
        if not cases:
            return "No similar prior cases found."
        return "\n".join(
            f"- {c.case_id}: {c.outcome} ({c.pattern}), ${c.exposure_usd:.2f}. "
            f"Notes: {c.analyst_notes[:150] if c.analyst_notes else 'none'}"
            for c in cases[:5]
        )

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def requests_remaining(self) -> int:
        if self._rate_limiter:
            return self._rate_limiter.requests_remaining_today
        return -1  # unlimited

    # ──────────────────────────────────────────────────────
    # Fallback methods (rule-based)
    # ──────────────────────────────────────────────────────

    def _fallback_assessment(self, state: InvestigationState) -> dict:
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
        return {
            "initial_actions": [],
            "final_actions": [],
            "what_changed": "nothing",
            "sar_required": False,
            "sar_reason": "LLM not available",
        }

    def _fallback_sar_narrative(self, state: InvestigationState, exposure: float) -> str:
        flagged = state.flagged_txn
        when = flagged.timestamp[:10] if flagged and flagged.timestamp else state.trigger.opened_at[:10]
        return (
            f"On {when}, customer {state.trigger.customer_id}, card {state.trigger.card_id} "
            f"was flagged for {state.pattern.value.replace('_', ' ')}. "
            f"Total exposure: ${exposure:.2f}. "
            f"Evidence: {'; '.join(e.claim for e in state.evidence_collected[:3])}."
        )

    def _fallback_explanation(self, state: InvestigationState) -> str:
        parts = []
        if state.flagged_txn:
            parts.append(
                f"Transaction {state.flagged_txn.transaction_id} for "
                f"${state.flagged_txn.amount:.2f} flagged via {state.trigger.trigger_type.value}."
            )
        if state.pattern != FraudPattern.NONE:
            parts.append(f"Pattern: {state.pattern.value.replace('_', ' ')}.")
        parts.append(f"Verdict: {state.verdict.value} ({state.fraud_probability:.2f}).")
        return " ".join(parts)
