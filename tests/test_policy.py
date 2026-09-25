"""Fraud Policy v1.0 — one test per rule. policy.decide() is pure, so these are exact."""
from src.engine.policy import PolicyInput, decide, route


def names(**kw):
    base = dict(p=0.5, verdict="uncertain", exposure=100.0)
    base.update(kw)
    return decide(PolicyInput(**base))


def test_routes_section2():
    assert route("BLOCK_CARD", 2500) == "L1"
    assert route("BLOCK_CARD", 2500.01) == "L2"
    assert route("FILE_REPORT", 10) == "L2"
    assert route("BLOCK_ALL_CARDS", 10) == "L2"
    assert route("DECLINE_TRANSACTION", 10) == "L1"
    for a in ("CREATE_CASE", "VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH", "CLOSE_NO_FRAUD", "ESCALATE_TO_ANALYST",
              "MONITOR_CONNECTED_CARDS", "WARN_CUSTOMER", "MONITOR_CARD", "ALLOW_TRANSACTION", "GENERATE_REPORT"):
        assert route(a, 99999) == "auto"


def test_r1_verify_before_block_on_single_signal():
    d = names(p=0.6, verdict="uncertain", n_signals=1)
    assert "VERIFY_WITH_CUSTOMER" in d.names()
    assert "BLOCK_CARD" not in d.names() and "DECLINE_TRANSACTION" not in d.names()


def test_block_allowed_when_strong_and_multi_signal():
    d = names(p=0.9, verdict="fraud", n_signals=3, exposure=400)
    assert d.names()[0] == "BLOCK_CARD" and "CREATE_CASE" in d.names()
    assert not d.file_sar           # $400, no shared origin: case only (3a)


def test_r2_denial_block_case_and_report_over_1000():
    d = names(p=0.9, verdict="fraud", response="denied", exposure=1200, n_signals=2)
    assert {"BLOCK_CARD", "CREATE_CASE", "FILE_REPORT"} <= set(d.names())
    d = names(p=0.9, verdict="fraud", response="denied", exposure=200, n_signals=2)
    assert "FILE_REPORT" not in d.names()


def test_r3_confirmation_closes():
    d = names(p=0.05, verdict="legitimate", response="confirmed", evidence_requested=True)
    assert "CLOSE_NO_FRAUD" in d.names()
    assert not ({"BLOCK_CARD", "FILE_REPORT", "DECLINE_TRANSACTION"} & set(d.names()))


def test_r4_no_reply_monitor_decline_escalate_over_500():
    d = names(p=0.5, response="no_reply", exposure=600, evidence_requested=True)
    assert {"MONITOR_CARD", "DECLINE_TRANSACTION", "ESCALATE_TO_ANALYST"} <= set(d.names())
    d = names(p=0.5, response="no_reply", exposure=400, evidence_requested=True)
    assert "ESCALATE_TO_ANALYST" not in d.names()


def test_r5_card_testing_block_only_if_over_100_cleared():
    d = names(p=0.9, verdict="fraud", pattern="card_testing", card_testing_cleared_max=90, n_signals=2)
    assert {"DECLINE_TRANSACTION", "STEP_UP_AUTH"} <= set(d.names()) and "BLOCK_CARD" not in d.names()
    d = names(p=0.9, verdict="fraud", pattern="card_testing", card_testing_cleared_max=150, n_signals=2)
    assert "BLOCK_CARD" in d.names()


def test_r6_shared_origin():
    d = names(p=0.9, verdict="fraud", shared_origin="device profile X", connected_cards=4, n_signals=3)
    assert {"CREATE_CASE", "FILE_REPORT", "MONITOR_CONNECTED_CARDS"} <= set(d.names())


def test_r7_recurring_dispute_never_blocks():
    d = names(p=0.5, trigger="customer_report", recurring_match=True)
    assert {"CREATE_CASE", "VERIFY_WITH_CUSTOMER", "WARN_CUSTOMER"} <= set(d.names())
    assert "BLOCK_CARD" not in d.names()


def test_r8_uncertain_and_exposed_escalates():
    d = names(p=0.5, verdict="uncertain", exposure=800)
    assert "ESCALATE_TO_ANALYST" in d.names()


def test_r9_undocumented_coordinated():
    d = names(p=0.9, verdict="fraud", pattern="undocumented", cross_customer=True, n_signals=3)
    assert {"CREATE_CASE", "FILE_REPORT", "ESCALATE_TO_ANALYST"} <= set(d.names())


def test_r10_never_block_all():
    for kw in (dict(p=0.99, verdict="fraud", response="denied", exposure=9000, n_signals=5),
               dict(p=0.99, verdict="fraud", pattern="undocumented", cross_customer=True)):
        assert "BLOCK_ALL_CARDS" not in names(**kw).names()


def test_3a_case_at_030_and_report_needs_strong_suspicion():
    assert "CREATE_CASE" in names(p=0.30, verdict="uncertain", n_signals=2).names()
    d = names(p=0.5, verdict="uncertain", exposure=5000, n_signals=2)
    assert "FILE_REPORT" not in d.names() and not d.file_sar


def test_every_action_cites_a_rule_and_sar_flag_matches():
    for kw in (dict(p=0.9, verdict="fraud", response="denied", exposure=3000, n_signals=2),
               dict(p=0.05, verdict="legitimate", response="confirmed"),
               dict(p=0.5, response="no_reply", exposure=900)):
        d = names(**kw)
        assert d.actions
        assert d.file_sar == ("FILE_REPORT" in d.names())
        for a in d.actions:
            assert a["reason"]
