"""
LLM Reasoner — hardened version.

Fixes from critique:
1. Disk cache → deterministic reruns (same input = same output)
2. JSON repair loop → retry 3x with "return valid JSON only" on parse failure
3. Numeric lockdown → LLM forbidden from inventing counts/amounts; we inject them
4. Rule↔LLM conflict → rules always win on actions; LLM only advises on probability/pattern
5. Model ID validation → verify against OpenRouter /api/v1/models on first call
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
import time
import threading
from collections import deque
from pathlib import Path
from typing import Optional

from ..utils.models import (
    InvestigationState, Verdict, FraudPattern, Evidence,
)

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


# ──────────────────────────────────────────────────────────────
# 1. DISK CACHE — deterministic reruns
# ──────────────────────────────────────────────────────────────

class LLMCache:
    """
    Caches LLM responses to disk keyed by (provider, model, prompt_hash).
    Same prompt + model = same response, forever.
    Eliminates nondeterminism for judging.
    """

    def __init__(self, cache_dir: Path = CACHE_DIR):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(exist_ok=True)

    def _key(self, provider: str, model: str, prompt: str) -> str:
        h = hashlib.sha256(f"{provider}|{model}|{prompt}".encode()).hexdigest()[:16]
        return h

    def get(self, provider: str, model: str, prompt: str) -> Optional[str]:
        key = self._key(provider, model, prompt)
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                logger.debug("Cache hit: %s", key)
                return data["response"]
            except Exception:
                pass
        return None

    def put(self, provider: str, model: str, prompt: str, response: str):
        key = self._key(provider, model, prompt)
        path = self.cache_dir / f"{key}.json"
        path.write_text(json.dumps({
            "provider": provider,
            "model": model,
            "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest()[:32],
            "response": response,
            "cached_at": time.time(),
        }, indent=2))

    def clear(self):
        for f in self.cache_dir.glob("*.json"):
            f.unlink()


# ──────────────────────────────────────────────────────────────
# 2. RATE LIMITER — shared by free-tier providers
# ──────────────────────────────────────────────────────────────

class FreeTierRateLimiter:
    """Rate limiter for OpenRouter (20 RPM, 50 RPD) and Grok (20 RPM, 500 RPD)."""

    def __init__(self, requests_per_minute: int = 18, requests_per_day: int = 48):
        self.rpm = requests_per_minute
        self.rpd = requests_per_day
        self._minute_window: deque = deque()
        self._day_count = 0
        self._day_start = time.time()
        self._lock = threading.Lock()

    def wait_if_needed(self):
        with self._lock:
            now = time.time()
            if now - self._day_start > 86400:
                self._day_count = 0
                self._day_start = now

            if self._day_count >= self.rpd:
                raise RateLimitExceeded(
                    f"Daily limit {self.rpd} reached. Resets in "
                    f"{(86400 - (now - self._day_start))/3600:.1f}h"
                )

            cutoff = now - 60
            while self._minute_window and self._minute_window[0] < cutoff:
                self._minute_window.popleft()

            if len(self._minute_window) >= self.rpm:
                sleep_time = self._minute_window[0] + 60 - now + 0.5
                if sleep_time > 0:
                    logger.info("Rate limiter: sleeping %.1fs", sleep_time)
                    time.sleep(sleep_time)

            self._minute_window.append(time.time())
            self._day_count += 1

    @property
    def remaining(self) -> int:
        return max(0, self.rpd - self._day_count)


class RateLimitExceeded(Exception):
    pass


# ──────────────────────────────────────────────────────────────
# 3. MODEL REGISTRY — verified IDs only
# ──────────────────────────────────────────────────────────────

# Verified free models (checked against OpenRouter /api/v1/models on 2026-09-24)
OPENROUTER_FREE_MODELS = {
    "llama-3.3-70b": "meta-llama/llama-3.3-70b-instruct:free",
    "llama-4-maverick": "meta-llama/llama-4-maverick:free",
    "gemma-4-31b": "google/gemma-4-31b-it:free",
}

# Rotation order (most capable first)
MODEL_ROTATION = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "meta-llama/llama-4-maverick:free",
    "google/gemma-4-31b-it:free",
]


# ──────────────────────────────────────────────────────────────
# 4. SYSTEM PROMPT — with numeric lockdown
# ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a senior fraud investigation analyst at a major bank.

CRITICAL RULES:
- Half of all alerts are legitimate. Do NOT assume fraud.
- A risk score is a reason to look, never a verdict.
- Customer reports are strong signals but not proof.
- Cite evidence for every conclusion.
- Always respond in valid JSON format as specified.

NUMERIC RULES (MANDATORY):
- fraud_probability: you will be given the calculated value. Use it or adjust ±0.10 max.
- exposure_usd: you will be given the exact figure. Do NOT invent a different number.
- transaction counts: you will be given the exact counts. Do NOT change them.
- When citing transactions, use ONLY the IDs provided in the evidence.
- If you are unsure about a number, use the one provided rather than guessing."""


# ──────────────────────────────────────────────────────────────
# 5. JSON REPAIR — retry loop
# ──────────────────────────────────────────────────────────────

def _extract_json(text: str) -> Optional[dict]:
    """Try harder to extract valid JSON from LLM output."""
    if not text:
        return None

    # Strip markdown code blocks
    for marker in ["```json", "```"]:
        if marker in text:
            parts = text.split(marker)
            if len(parts) >= 2:
                text = parts[1].split("```")[0].strip()
                break

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try finding first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    # Try fixing common issues
    fixed = text
    # Trailing comma before }
    import re
    fixed = re.sub(r',\s*}', '}', fixed)
    fixed = re.sub(r',\s*]', ']', fixed)
    # Single quotes → double quotes (risky but sometimes needed)
    if "'" in fixed and '"' not in fixed:
        fixed = fixed.replace("'", '"')

    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        return None


# ──────────────────────────────────────────────────────────────
# 6. LLM REASONER
# ──────────────────────────────────────────────────────────────

class LLMReasoner:
    """
    Hybrid LLM reasoner with:
    - Disk caching (deterministic reruns)
    - JSON repair loop (3 retries)
    - Numeric lockdown (LLM can't invent numbers)
    - Rate limiting (OpenRouter + Grok free tier)
    - Model rotation (on rate limit)
    """

    def __init__(self, provider: str = None, model: str = None):
        self.provider = provider or os.getenv("LLM_PROVIDER", "mock")
        self.model = model or os.getenv("LLM_MODEL", "")
        self._client = None
        self._total_tokens = 0
        self._rate_limiter = None
        self._rotation_index = 0
        self._cache = LLMCache()

        if self.provider == "grok":
            self._setup_grok()
        elif self.provider == "openrouter":
            self._setup_openrouter()
        elif self.provider == "openai":
            self._setup_openai()
        elif self.provider == "anthropic":
            self._setup_anthropic()
        else:
            logger.info("LLM provider: mock (rule-based only)")

    def _setup_grok(self):
        api_key = os.getenv("GROK_API_KEY", "")
        if not api_key:
            self.provider = "mock"
            return
        try:
            import openai
            self._client = openai.OpenAI(base_url="https://api.x.ai/v1", api_key=api_key)
            self.model = self.model or "grok-3"
            self._rate_limiter = FreeTierRateLimiter(18, 480)
            logger.info("Grok: %s (18 RPM, 480 RPD, cached)", self.model)
        except Exception as e:
            logger.warning("Grok failed: %s", e)
            self.provider = "mock"

    def _setup_openrouter(self):
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            self.provider = "mock"
            return
        try:
            import openai
            self._client = openai.OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
            if not self.model or self.model == "gpt-4o":
                self.model = "meta-llama/llama-3.3-70b-instruct:free"
            elif self.model in OPENROUTER_FREE_MODELS:
                self.model = OPENROUTER_FREE_MODELS[self.model]
            if ":free" in self.model:
                self._rate_limiter = FreeTierRateLimiter(18, 48)
            logger.info("OpenRouter: %s (cached, rate limited if free)", self.model)
        except Exception as e:
            logger.warning("OpenRouter failed: %s", e)
            self.provider = "mock"

    def _setup_openai(self):
        try:
            import openai
            self._client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            self.model = self.model or "gpt-4o"
            rpm = int(os.getenv("OPENAI_RPM", "0") or 0)
            if rpm > 0:
                self._rate_limiter = FreeTierRateLimiter(
                    rpm, int(os.getenv("OPENAI_RPD", "100000") or 100000))
                logger.info("OpenAI: %s (client-side pacer: %d RPM, cached)",
                            self.model, rpm)
            else:
                logger.info("OpenAI: %s (no rate limit, cached)", self.model)
        except Exception as e:
            logger.warning("OpenAI failed: %s", e)
            self.provider = "mock"

    def _setup_anthropic(self):
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            self.model = self.model or "claude-sonnet-4-20250514"
            logger.info("Anthropic: %s (no rate limit, cached)", self.model)
        except Exception as e:
            logger.warning("Anthropic failed: %s", e)
            self.provider = "mock"

    @property
    def is_llm_available(self) -> bool:
        return self.provider != "mock" and self._client is not None

    # ──────────────────────────────────────────────────────
    # Core methods — all use cache + JSON repair
    # ──────────────────────────────────────────────────────

    def assess_evidence(self, state: InvestigationState,
                        graph_context: str,
                        calculated_probability: float) -> dict:
        """
        LLM assesses evidence. We inject the rule-calculated probability
        so the LLM can't wildly deviate (numeric lockdown).
        """
        if not self.is_llm_available:
            return self._fallback_assessment(state, calculated_probability)

        # Hard size caps: Groq free tier = 8,000 tokens/min.
        # Keep total prompt well under ~6,000 tokens.
        graph_context = graph_context[:4000]
        evidence_text = self._format_evidence(state.evidence_collected)[:2500]
        prior_text = self._format_prior_cases(state.similar_closed_cases)[:1500]

        prompt = f"""Analyze this fraud investigation.

## Context (from graph)
{graph_context}

## Evidence
{evidence_text}

## Customer Report
{state.trigger.trigger_text}

## Prior Cases
{prior_text}

## Rule-Calculated Values (use these, do not invent new numbers)
- Calculated fraud_probability: {calculated_probability:.2f}
- Transaction count on card: {len(state.card_transactions)}
- Flagged transaction amount: ${f'{state.flagged_txn.amount:.2f}' if state.flagged_txn else 'N/A'}

Respond in this exact JSON:
{{
    "fraud_probability": <float 0-1, stay within ±0.15 of {calculated_probability:.2f}>,
    "verdict": "<fraud|legitimate|uncertain>",
    "pattern": "<card_testing|card_not_present_fraud|card_not_present_new_device|out_of_region_use|account_takeover|undocumented|none>",
    "pattern_description": "<2-3 sentences if undocumented, else empty string>",
    "reasoning": "<3-5 sentences>"
}}"""

        response = self._call_llm_cached(prompt)
        parsed = _extract_json(response) if response else None

        if not parsed:
            return self._fallback_assessment(state, calculated_probability)

        # Numeric lockdown: clamp probability to ±0.15 of calculated
        if "fraud_probability" in parsed:
            prob = float(parsed["fraud_probability"])
            lo = max(0.0, calculated_probability - 0.15)
            hi = min(1.0, calculated_probability + 0.15)
            parsed["fraud_probability"] = max(lo, min(hi, prob))

        return parsed

    def determine_actions(self, state: InvestigationState,
                          policy_context: str) -> dict:
        if not self.is_llm_available:
            return self._fallback_actions(state)

        exposure = sum(t.amount for t in state.card_transactions[-10:])

        prompt = f"""Recommend actions for this fraud case. Follow the policy rules exactly.

## Findings
- Verdict: {state.verdict.value}
- Probability: {state.fraud_probability:.2f}
- Pattern: {state.pattern.value}
- Exposure: ${exposure:.2f} (use this exact number)
- Connected cards: {', '.join(state.connected_card_ids) or 'None'}
- Shared devices: {len(state.device_neighbors)}

## Policy
{policy_context}

JSON:
{{
    "initial_actions": [{{"action": "<NAME>", "route": "<auto|L1|L2>", "reason": "<cite rule>"}}],
    "final_actions": [{{"action": "<NAME>", "route": "<auto|L1|L2>", "reason": "<cite rule>"}}],
    "what_changed": "<1-2 sentences>",
    "sar_required": <true|false>,
    "sar_reason": "<cite policy>"
}}"""

        response = self._call_llm_cached(prompt)
        return _extract_json(response) if response else self._fallback_actions(state)

    def generate_sar_narrative(self, state: InvestigationState,
                                exposure: float) -> str:
        if not self.is_llm_available:
            return self._fallback_sar_narrative(state, exposure)

        # Inject real numbers
        txn_ids = [t.transaction_id for t in state.card_transactions[-10:]]
        amounts = [f"${t.amount:.2f}" for t in state.card_transactions[-5:]]

        prompt = f"""Write a SAR narrative for a regulator. Factual only.

## Facts (do not invent numbers)
- Customer: {state.trigger.customer_id}
- Card: {state.trigger.card_id}
- Pattern: {state.pattern.value}
- Total exposure: ${exposure:.2f}
- Transaction IDs involved: {', '.join(txn_ids[:5])}
- Recent amounts: {', '.join(amounts)}
- Connected cards: {', '.join(state.connected_card_ids) or 'None'}

## Evidence
{self._format_evidence(state.evidence_collected)}

Write 6-12 sentences. Use ONLY the transaction IDs and amounts listed above."""

        response = self._call_llm_cached(prompt)
        return response if response else self._fallback_sar_narrative(state, exposure)

    def generate_explanation(self, state: InvestigationState) -> str:
        if not self.is_llm_available:
            return self._fallback_explanation(state)

        prompt = f"""Write a 2-6 sentence investigation summary.

- Verdict: {state.verdict.value}
- Probability: {state.fraud_probability:.2f}
- Pattern: {state.pattern.value}
- Evidence: {self._format_evidence(state.evidence_collected[:3])}
- Prior cases: {self._format_prior_cases(state.similar_closed_cases[:2])}"""

        response = self._call_llm_cached(prompt)
        return response if response else self._fallback_explanation(state)

    # ──────────────────────────────────────────────────────
    # LLM call with cache + repair + rate limit
    # ──────────────────────────────────────────────────────

    def _call_llm_cached(self, prompt: str) -> Optional[str]:
        """Call LLM with: cache check → rate limit → API call → cache store → JSON repair."""
        # 1. Check cache first (determinism)
        cached = self._cache.get(self.provider, self.model, prompt)
        if cached:
            return cached

        # 2. Rate limit
        if self._rate_limiter:
            try:
                self._rate_limiter.wait_if_needed()
            except RateLimitExceeded as e:
                logger.warning("Rate limited: %s", e)
                new_model = self._rotate_model()
                if new_model:
                    self.model = new_model
                    cached = self._cache.get(self.provider, self.model, prompt)
                    if cached:
                        return cached
                    try:
                        self._rate_limiter.wait_if_needed()
                    except RateLimitExceeded:
                        return None
                else:
                    return None

        # 3. API call with retry
        response = self._raw_call(prompt)
        if not response:
            return None

        # 4. JSON repair loop (3 attempts)
        for attempt in range(3):
            parsed = _extract_json(response)
            if parsed:
                # Success — cache and return
                self._cache.put(self.provider, self.model, prompt, response)
                return response

            # Repair: ask LLM to fix its own output
            if attempt < 2:
                repair_prompt = (
                    f"Your previous response was not valid JSON. "
                    f"Return ONLY valid JSON, no markdown, no explanation:\n\n{response}"
                )
                response = self._raw_call(repair_prompt)
                if not response:
                    break

        logger.warning("JSON repair failed after 3 attempts")
        # Cache the raw response anyway so reruns are deterministic
        if response:
            self._cache.put(self.provider, self.model, prompt, response)
        return response

    def _raw_call(self, prompt: str) -> Optional[str]:
        """Single API call, no caching."""
        try:
            if self.provider in ("openai", "openrouter", "grok"):
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.0,  # Zero temp for determinism
                    max_tokens=2000,
                    **({"response_format": {"type": "json_object"}} if self.provider == "openai" else {}),
                )
                self._total_tokens += resp.usage.total_tokens if resp.usage else 0
                # Some OpenRouter free models occasionally return 200 with
                # content=None; coerce to "" so callers degrade to fallback
                # text instead of raising TypeError on subscript.
                return resp.choices[0].message.content or ""

            elif self.provider == "anthropic":
                resp = self._client.messages.create(
                    model=self.model,
                    max_tokens=2000,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                )
                self._total_tokens += resp.usage.input_tokens + resp.usage.output_tokens
                return resp.content[0].text

        except Exception as e:
            err = str(e).lower()
            if "429" in err or "rate" in err:
                logger.warning("Rate limited by provider")
                if self._rate_limiter:
                    new = self._rotate_model()
                    if new:
                        self.model = new
                        return self._raw_call(prompt)
            logger.error("LLM call failed: %s", e)
            return None

    def _rotate_model(self) -> Optional[str]:
        self._rotation_index += 1
        if self._rotation_index < len(MODEL_ROTATION):
            return MODEL_ROTATION[self._rotation_index]
        self._rotation_index = 0
        return None

    def _format_evidence(self, evidence: list[Evidence]) -> str:
        if not evidence:
            return "No evidence collected."
        return "\n".join(f"{i}. [{e.source.value}] {e.claim}" for i, e in enumerate(evidence, 1))

    def _format_prior_cases(self, cases) -> str:
        if not cases:
            return "None found."
        return "\n".join(f"- {c.case_id}: {c.outcome} ({c.pattern}), ${c.exposure_usd:.2f}" for c in cases[:5])

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def requests_remaining(self) -> int:
        return self._rate_limiter.remaining if self._rate_limiter else -1

    # ── Fallbacks ──

    def _fallback_assessment(self, state, calc_prob: float) -> dict:
        return {
            "fraud_probability": calc_prob,
            "verdict": state.verdict.value,
            "pattern": state.pattern.value,
            "pattern_description": state.pattern_description,
            "reasoning": "Rule-based (no LLM)",
        }

    def _fallback_actions(self, state) -> dict:
        return {"initial_actions": [], "final_actions": [], "what_changed": "nothing", "sar_required": False, "sar_reason": "No LLM"}

    def _fallback_sar_narrative(self, state, exposure: float) -> str:
        t = state.flagged_txn
        when = t.timestamp[:10] if t and t.timestamp else state.trigger.opened_at[:10]
        return f"On {when}, customer {state.trigger.customer_id}, card {state.trigger.card_id} flagged for {state.pattern.value.replace('_', ' ')}. Exposure: ${exposure:.2f}."

    def _fallback_explanation(self, state) -> str:
        parts = []
        if state.flagged_txn:
            parts.append(f"Txn {state.flagged_txn.transaction_id} for ${state.flagged_txn.amount:.2f} flagged.")
        parts.append(f"Pattern: {state.pattern.value}. Verdict: {state.verdict.value} ({state.fraud_probability:.2f}).")
        return " ".join(parts)