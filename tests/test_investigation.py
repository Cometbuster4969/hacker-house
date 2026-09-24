"""Tests for the fraud investigation agent."""
import json
import sys
from pathlib import Path

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.graph.in_memory_graph import InMemoryGraph
from src.utils.models import (
    CasePackEntry, TriggerType, Transaction, InvestigationState,
    Verdict, FraudPattern, ActionType, ApprovalRoute,
)
from src.evidence.gatherer import EvidenceGatherer
from src.evidence.pattern_detector import PatternDetector
from src.policy.engine import PolicyEngine
from src.mcp.server import TigerGraphMCPServer


@pytest.fixture
def graph():
    """Create a test graph with sample data."""
    g = InMemoryGraph()

    # Add customers
    g.add_vertex("Customer", "C00001", {"customer_id": "C00001"})
    g.add_vertex("Customer", "C00002", {"customer_id": "C00002"})

    # Add cards
    g.add_vertex("Card", "C00001-K1", {"card_id": "C00001-K1", "customer_id": "C00001", "network": "visa", "card_type": "credit"})
    g.add_vertex("Card", "C00002-K1", {"card_id": "C00002-K1", "customer_id": "C00002", "network": "mastercard", "card_type": "debit"})

    # Add edges
    g.add_edge("Customer", "C00001", "OWNS", "Card", "C00001-K1")
    g.add_edge("Customer", "C00002", "OWNS", "Card", "C00002-K1")

    # Build customer-card index
    g._customer_card_index["C00001"] = ["C00001-K1"]
    g._customer_card_index["C00002"] = ["C00002-K1"]

    # Add transactions (card testing pattern)
    for i, (amt, ts) in enumerate([
        (1.10, "2016-11-15 10:00:00"),
        (2.40, "2016-11-15 10:15:00"),
        (0.95, "2016-11-15 10:30:00"),
        (259.98, "2016-11-15 11:00:00"),
    ]):
        txn_id = f"T{i+1:07d}"
        g.add_vertex("Transaction", txn_id, {
            "id": txn_id, "transaction_id": txn_id,
            "customer_id": "C00001", "card_id": "C00001-K1",
            "amount": amt, "ts": ts, "channel": "online",
            "risk_score": 0.8, "product_cd": "C",
            "addr1": 444.0, "addr2": 87.0,
            "p_emaildomain": "gmail.com", "r_emaildomain": "",
        })
        g.add_edge("Card", "C00001-K1", "MADE", "Transaction", txn_id)
        g._card_txn_index["C00001-K1"].append(txn_id)

    # Add device
    g.add_vertex("DeviceProfile", "D000001", {
        "device_id": "D000001", "device_info": "Samsung Galaxy S8",
        "os": "Android 8.0", "browser": "chrome", "screen": "2220x1080",
    })
    g.add_edge("Transaction", "T0000001", "FROM_DEVICE", "DeviceProfile", "D000001")
    g.add_edge("Transaction", "T0000002", "FROM_DEVICE", "DeviceProfile", "D000001")
    g.add_edge("Transaction", "T0000003", "FROM_DEVICE", "DeviceProfile", "D000001")
    g.add_edge("Transaction", "T0000004", "FROM_DEVICE", "DeviceProfile", "D000001")
    g._device_txn_index["D000001"] = ["T0000001", "T0000002", "T0000003", "T0000004"]

    # Add billing region
    g.add_vertex("BillingRegion", "444", {"region_id": "444", "country_code": 87.0})
    g.add_edge("Transaction", "T0000004", "BILLED_IN", "BillingRegion", "444")
    g._region_txn_index["444"] = ["T0000004"]

    # Add closed case
    g.add_vertex("ClosedCase", "CC-0001", {
        "case_id": "CC-0001", "customer_id": "C00001", "card_id": "C00001-K1",
        "opened_at": "2016-08-01", "closed_at": "2016-08-15",
        "outcome": "confirmed_fraud", "pattern": "card_testing",
        "exposure_usd": 500.0, "analyst_notes": "Card testing confirmed",
    })

    return g


@pytest.fixture
def case_pack_entry():
    """Create a test case pack entry."""
    return CasePackEntry(
        case_id="HHG-TEST",
        opened_at="2016-11-15 12:00:00",
        trigger_type=TriggerType.RISK_SCORE,
        trigger_text="Risk score 0.80 on transaction T0000004",
        flagged_txn_id="T0000004",
        card_id="C00001-K1",
        customer_id="C00001",
        risk_score=0.80,
    )


class TestInMemoryGraph:
    """Test the in-memory graph engine."""

    def test_add_vertex(self, graph):
        assert graph.get_vertex("Customer", "C00001") is not None
        assert graph.get_vertex("Customer", "C00001")["customer_id"] == "C00001"

    def test_add_edge(self, graph):
        neighbors = graph.get_neighbors("Customer", "C00001", "OWNS", "Card")
        assert len(neighbors) == 1
        assert neighbors[0]["card_id"] == "C00001-K1"

    def test_card_transactions(self, graph):
        txns = graph.get_card_transactions("C00001-K1")
        assert len(txns) == 4
        assert txns[0]["amount"] == 1.10
        assert txns[-1]["amount"] == 259.98

    def test_customer_cards(self, graph):
        cards = graph.get_customer_cards("C00001")
        assert len(cards) == 1
        assert cards[0]["card_id"] == "C00001-K1"

    def test_device_cards(self, graph):
        cards = graph.get_device_cards("D000001")
        assert len(cards) >= 1

    def test_detect_card_testing(self, graph):
        result = graph.detect_card_testing("C00001-K1")
        assert result["detected"] is True
        assert result["pattern"] == "card_testing"
        assert len(result["small_txns"]) >= 2

    def test_card_velocity(self, graph):
        velocity = graph.get_card_velocity("C00001-K1", hours=24)
        assert velocity["velocity_24h"] == 4

    def test_graph_stats(self, graph):
        stats = graph.get_graph_stats()
        assert stats["vertices_Customer"] == 2
        assert stats["vertices_Card"] == 2
        assert stats["vertices_Transaction"] == 4

    def test_closed_cases(self, graph):
        cases = graph.get_closed_cases_for_customer("C00001")
        assert len(cases) == 1
        assert cases[0]["outcome"] == "confirmed_fraud"


class TestPatternDetector:
    """Test fraud pattern detection."""

    def test_card_testing_detection(self, graph, case_pack_entry):
        gatherer = EvidenceGatherer(graph)
        state = InvestigationState(case_id="HHG-TEST", trigger=case_pack_entry)
        state = gatherer.gather_initial_evidence(state)

        detector = PatternDetector()
        state = detector.detect_patterns(state)
        assert state.pattern == FraudPattern.CARD_TESTING

    def test_probability_calculation(self, graph, case_pack_entry):
        gatherer = EvidenceGatherer(graph)
        state = InvestigationState(case_id="HHG-TEST", trigger=case_pack_entry)
        state = gatherer.gather_initial_evidence(state)

        detector = PatternDetector()
        state = detector.detect_patterns(state)
        prob = detector.calculate_fraud_probability(state)
        assert 0.0 <= prob <= 1.0
        assert prob > 0.5  # Should be above neutral given the pattern


class TestPolicyEngine:
    """Test the policy engine."""

    def test_initial_actions_for_high_risk(self, graph, case_pack_entry):
        engine = PolicyEngine()
        state = InvestigationState(
            case_id="HHG-TEST",
            trigger=case_pack_entry,
            fraud_probability=0.85,
            pattern=FraudPattern.CARD_TESTING,
        )
        nba = engine.evaluate_initial_actions(state)
        assert len(nba.initial) > 0
        # Should include card testing actions
        action_types = [a.action for a in nba.initial]
        assert ActionType.DECLINE_TRANSACTION in action_types or ActionType.BLOCK_CARD in action_types

    def test_initial_actions_for_low_risk(self, graph):
        engine = PolicyEngine()
        entry = CasePackEntry(
            case_id="HHG-TEST-LOW",
            opened_at="2016-11-15 12:00:00",
            trigger_type=TriggerType.RISK_SCORE,
            trigger_text="Low risk",
            flagged_txn_id="T0000004",
            card_id="C00001-K1",
            customer_id="C00001",
            risk_score=0.30,
        )
        state = InvestigationState(
            case_id="HHG-TEST-LOW",
            trigger=entry,
            fraud_probability=0.15,
        )
        nba = engine.evaluate_initial_actions(state)
        action_types = [a.action for a in nba.initial]
        assert ActionType.MONITOR_CARD in action_types

    def test_sar_not_filed_for_legitimate(self):
        engine = PolicyEngine()
        state = InvestigationState(
            case_id="TEST",
            trigger=CasePackEntry(
                case_id="TEST", opened_at="2016-01-01",
                trigger_type=TriggerType.RISK_SCORE, trigger_text="",
                flagged_txn_id="", card_id="", customer_id="",
            ),
            verdict=Verdict.LEGITIMATE,
        )
        sar = engine.evaluate_sar(state)
        assert sar.file is False

    def test_approval_routes(self):
        engine = PolicyEngine()
        state = InvestigationState(
            case_id="TEST",
            trigger=CasePackEntry(
                case_id="TEST", opened_at="2016-01-01",
                trigger_type=TriggerType.CUSTOMER_REPORT,
                trigger_text="Customer denies",
                flagged_txn_id="T001", card_id="C01-K1",
                customer_id="C01",
            ),
            verdict=Verdict.FRAUD,
            fraud_probability=0.90,
            pattern=FraudPattern.CARD_NOT_PRESENT_FRAUD,
            flagged_txn=Transaction(
                transaction_id="T001", customer_id="C01",
                card_id="C01-K1", amount=500.0,
                timestamp="2016-11-15 10:00:00", channel="online",
                risk_score=0.9,
            ),
            evidence_requests=[],
        )
        nba = engine.evaluate_final_actions(state, [])
        routes = {a.action: a.route for a in nba.final}
        # BLOCK_CARD should be L1 or L2
        if ActionType.BLOCK_CARD in routes:
            assert routes[ActionType.BLOCK_CARD] in (ApprovalRoute.L1, ApprovalRoute.L2)


class TestMCPServer:
    """Test the MCP server."""

    def test_list_tools(self, graph):
        server = TigerGraphMCPServer(graph)
        tools = server.list_tools()
        assert len(tools) >= 10
        tool_names = [t["name"] for t in tools]
        assert "get_card_transactions" in tool_names
        assert "detect_card_testing" in tool_names

    def test_call_tool(self, graph):
        server = TigerGraphMCPServer(graph)
        result = server.call_tool("get_card_transactions", {"card_id": "C00001-K1"})
        assert len(result) == 4

    def test_detect_card_testing_tool(self, graph):
        server = TigerGraphMCPServer(graph)
        result = server.call_tool("detect_card_testing", {"card_id": "C00001-K1"})
        assert result["detected"] is True

    def test_unknown_tool(self, graph):
        server = TigerGraphMCPServer(graph)
        result = server.call_tool("nonexistent_tool", {})
        assert "error" in result

    def test_llm_format(self, graph):
        server = TigerGraphMCPServer(graph)
        tools = server.get_tools_for_llm()
        assert all(t["type"] == "function" for t in tools)
        assert all("function" in t for t in tools)


class TestCaseAnswerFormat:
    """Test that answers conform to the required format."""

    def test_answer_has_required_fields(self):
        """Verify the answer JSON has all required top-level fields."""
        answer_path = Path(__file__).parent.parent / "cases" / "HHG-001.json"
        if not answer_path.exists():
            pytest.skip("No answer files generated yet")

        with open(answer_path) as f:
            data = json.load(f)

        # Top-level fields
        assert "case_id" in data
        assert "case" in data
        assert "evidence_requests" in data
        assert "next_best_actions" in data
        assert "sar" in data
        assert "stop_reason" in data

        # Case fields
        case = data["case"]
        assert "status" in case
        assert "verdict" in case
        assert "fraud_probability" in case
        assert "pattern" in case
        assert "evidence" in case
        assert "summary" in case
        assert "written_to_graph" in case

        # NBA fields
        nba = data["next_best_actions"]
        assert "initial" in nba
        assert "final" in nba
        assert "what_changed" in nba

        # SAR fields
        sar = data["sar"]
        assert "file" in sar
        assert "reason" in sar

    def test_verdict_values(self):
        """Verify verdicts use valid values."""
        cases_dir = Path(__file__).parent.parent / "cases"
        if not cases_dir.exists():
            pytest.skip("No answer files generated yet")

        for f in cases_dir.glob("*.json"):
            with open(f) as fh:
                data = json.load(fh)
            assert data["case"]["verdict"] in ("fraud", "legitimate", "uncertain")

    def test_pattern_values(self):
        """Verify patterns use valid values."""
        cases_dir = Path(__file__).parent.parent / "cases"
        if not cases_dir.exists():
            pytest.skip("No answer files generated yet")

        valid_patterns = {
            "card_testing", "card_not_present_fraud",
            "card_not_present_new_device", "out_of_region_use",
            "account_takeover", "undocumented", "none",
        }
        for f in cases_dir.glob("*.json"):
            with open(f) as fh:
                data = json.load(fh)
            assert data["case"]["pattern"] in valid_patterns, \
                f"Invalid pattern in {f.name}: {data['case']['pattern']}"
