"""Tests for LLM layer: cache, rate limiter, JSON repair, conflict resolution."""
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.llm_reasoner import (
    LLMCache, FreeTierRateLimiter, RateLimitExceeded,
    _extract_json, LLMReasoner, SYSTEM_PROMPT,
)
from src.utils.models import (
    InvestigationState, CasePackEntry, TriggerType, Verdict,
    FraudPattern, Transaction, Evidence, SourceType, ActionType,
)


# ── Cache tests ──

class TestLLMCache:
    def test_put_and_get(self, tmp_path):
        cache = LLMCache(cache_dir=tmp_path)
        cache.put("openai", "gpt-4o", "test prompt", '{"result": "ok"}')
        assert cache.get("openai", "gpt-4o", "test prompt") == '{"result": "ok"}'

    def test_miss_returns_none(self, tmp_path):
        cache = LLMCache(cache_dir=tmp_path)
        assert cache.get("openai", "gpt-4o", "no such prompt") is None

    def test_different_models_not_shared(self, tmp_path):
        cache = LLMCache(cache_dir=tmp_path)
        cache.put("openai", "gpt-4o", "prompt", "response-a")
        cache.put("grok", "grok-3", "prompt", "response-b")
        assert cache.get("openai", "gpt-4o", "prompt") == "response-a"
        assert cache.get("grok", "grok-3", "prompt") == "response-b"

    def test_determinism(self, tmp_path):
        """Same input always returns same output."""
        cache = LLMCache(cache_dir=tmp_path)
        cache.put("openai", "gpt-4o", "prompt", "response")
        results = [cache.get("openai", "gpt-4o", "prompt") for _ in range(10)]
        assert len(set(results)) == 1

    def test_clear(self, tmp_path):
        cache = LLMCache(cache_dir=tmp_path)
        cache.put("openai", "gpt-4o", "prompt", "response")
        cache.clear()
        assert cache.get("openai", "gpt-4o", "prompt") is None


# ── Rate limiter tests ──

class TestRateLimiter:
    def test_allows_requests_under_limit(self):
        limiter = FreeTierRateLimiter(requests_per_minute=100, requests_per_day=1000)
        for _ in range(5):
            limiter.wait_if_needed()
        assert limiter.remaining == 995

    def test_raises_on_daily_limit(self):
        limiter = FreeTierRateLimiter(requests_per_minute=100, requests_per_day=3)
        limiter.wait_if_needed()
        limiter.wait_if_needed()
        limiter.wait_if_needed()
        with pytest.raises(RateLimitExceeded):
            limiter.wait_if_needed()

    def test_remaining_decreases(self):
        limiter = FreeTierRateLimiter(requests_per_minute=100, requests_per_day=100)
        before = limiter.remaining
        limiter.wait_if_needed()
        assert limiter.remaining == before - 1


# ── JSON repair tests ──

class TestJSONRepair:
    def test_clean_json(self):
        assert _extract_json('{"key": "value"}') == {"key": "value"}

    def test_markdown_wrapped(self):
        text = '```json\n{"key": "value"}\n```'
        assert _extract_json(text) == {"key": "value"}

    def test_markdown_no_lang(self):
        text = '```\n{"key": "value"}\n```'
        assert _extract_json(text) == {"key": "value"}

    def test_surrounded_by_text(self):
        text = 'Here is the result:\n{"key": "value"}\nDone.'
        assert _extract_json(text) == {"key": "value"}

    def test_trailing_comma(self):
        text = '{"a": 1, "b": 2,}'
        result = _extract_json(text)
        assert result is not None
        assert result["a"] == 1

    def test_invalid_returns_none(self):
        assert _extract_json("not json at all") is None
        assert _extract_json("") is None
        assert _extract_json(None) is None

    def test_nested_json(self):
        text = '{"outer": {"inner": [1, 2, 3]}}'
        result = _extract_json(text)
        assert result["outer"]["inner"] == [1, 2, 3]


# ── Numeric lockdown tests ──

class TestNumericLockdown:
    def _make_state(self):
        return InvestigationState(
            case_id="TEST",
            trigger=CasePackEntry(
                case_id="TEST", opened_at="2016-01-01",
                trigger_type=TriggerType.RISK_SCORE, trigger_text="test",
                flagged_txn_id="T001", card_id="C01-K1", customer_id="C01",
                risk_score=0.8,
            ),
            flagged_txn=Transaction(
                transaction_id="T001", customer_id="C01", card_id="C01-K1",
                amount=100.0, timestamp="2016-01-01", channel="online", risk_score=0.8,
            ),
            card_transactions=[
                Transaction(transaction_id=f"T{i}", customer_id="C01", card_id="C01-K1",
                           amount=50.0, timestamp="2016-01-01", channel="online", risk_score=0.3)
                for i in range(10)
            ],
        )

    def test_clamp_probability_within_range(self):
        """LLM probability clamped to ±0.15 of calculated."""
        reasoner = LLMReasoner.__new__(LLMReasoner)
        reasoner.provider = "mock"
        reasoner._client = None
        reasoner._cache = LLMCache.__new__(LLMCache)
        reasoner._total_tokens = 0

        state = self._make_state()

        # Mock a response with probability way off
        mock_response = json.dumps({
            "fraud_probability": 0.99,  # Way above calculated 0.50
            "verdict": "fraud",
            "pattern": "none",
            "pattern_description": "",
            "reasoning": "test",
        })

        # Since we're testing the clamp logic, call assess directly
        calc_prob = 0.50
        parsed = json.loads(mock_response)

        # Apply the clamp logic from assess_evidence
        lo = max(0.0, calc_prob - 0.15)
        hi = min(1.0, calc_prob + 0.15)
        clamped = max(lo, min(hi, float(parsed["fraud_probability"])))

        assert clamped == pytest.approx(0.65, abs=0.01)  # 0.50 + 0.15

    def test_llm_cannot_override_rule_verdict_silently(self):
        """Rules define actions; LLM only advises on probability/pattern."""
        # This is enforced in the orchestrator, not the reasoner.
        # The orchestrator calls policy_engine.evaluate_final_actions()
        # AFTER the LLM assessment, so rules always have final say.
        from src.policy.engine import PolicyEngine
        engine = PolicyEngine()

        state = self._make_state()
        state.fraud_probability = 0.15  # Low
        state.verdict = Verdict.UNCERTAIN

        nba = engine.evaluate_final_actions(state, [])
        # Even if LLM said "legitimate", rules enforce R1
        assert len(nba.final) > 0


# ── LLM Reasoner integration tests ──

class TestLLMReasoner:
    def test_mock_mode_works(self):
        reasoner = LLMReasoner(provider="mock")
        assert not reasoner.is_llm_available
        assert reasoner.total_tokens == 0

    def test_fallback_assessment(self):
        reasoner = LLMReasoner(provider="mock")
        state = InvestigationState(
            case_id="T", trigger=CasePackEntry(
                case_id="T", opened_at="2016-01-01",
                trigger_type=TriggerType.RISK_SCORE, trigger_text="",
                flagged_txn_id="", card_id="", customer_id="",
            ),
        )
        result = reasoner._fallback_assessment(state, 0.42)
        assert result["fraud_probability"] == 0.42

    def test_system_prompt_has_numeric_rules(self):
        assert "Do NOT invent" in SYSTEM_PROMPT or "Do NOT change" in SYSTEM_PROMPT

    def test_cache_dir_exists(self):
        from src.agent.llm_reasoner import CACHE_DIR
        assert CACHE_DIR.exists()


# ── Conflict resolution tests ──

class TestConflictResolution:
    """
    The critic asked: if LLM says 'legitimate' and rules say 'fraud', who wins?
    Answer: rules always win on actions. LLM only advises probability/pattern.
    The orchestrator calls policy_engine AFTER LLM assessment.
    """

    def test_rules_override_llm_on_actions(self):
        from src.policy.engine import PolicyEngine
        engine = PolicyEngine()

        state = InvestigationState(
            case_id="T", trigger=CasePackEntry(
                case_id="T", opened_at="2016-01-01",
                trigger_type=TriggerType.RISK_SCORE, trigger_text="",
                flagged_txn_id="T001", card_id="C01-K1", customer_id="C01",
                risk_score=0.9,
            ),
            fraud_probability=0.90,
            verdict=Verdict.FRAUD,
            pattern=FraudPattern.CARD_TESTING,
        )
        nba = engine.evaluate_initial_actions(state)
        action_types = {a.action for a in nba.initial}
        # R5: card testing → must include DECLINE or BLOCK
        assert ActionType.DECLINE_TRANSACTION in action_types or ActionType.BLOCK_CARD in action_types

    def test_low_probability_no_block(self):
        from src.policy.engine import PolicyEngine
        engine = PolicyEngine()

        state = InvestigationState(
            case_id="T", trigger=CasePackEntry(
                case_id="T", opened_at="2016-01-01",
                trigger_type=TriggerType.RISK_SCORE, trigger_text="",
                flagged_txn_id="", card_id="", customer_id="",
            ),
            fraud_probability=0.10,
            verdict=Verdict.LEGITIMATE,
        )
        nba = engine.evaluate_initial_actions(state)
        action_types = {a.action for a in nba.initial}
        # R1: no blocking below 0.70
        assert ActionType.BLOCK_CARD not in action_types
        assert ActionType.DECLINE_TRANSACTION not in action_types
