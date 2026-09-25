"""Fraud Policy v1.0 as a pure function.

``decide(PolicyInput) -> Decision`` has no I/O, no randomness and no LLM: the same case
state always yields the same, correctly routed, rule-cited action list. It is the only
writer of ``next_best_actions`` and of ``sar.file``; tests/test_policy.py pins every rule.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config as C

ORDER = ["DECLINE_TRANSACTION", "BLOCK_CARD", "BLOCK_ALL_CARDS", "STEP_UP_AUTH", "VERIFY_WITH_CUSTOMER",
         "MONITOR_CARD", "CREATE_CASE", "FILE_REPORT", "MONITOR_CONNECTED_CARDS", "ESCALATE_TO_ANALYST",
         "WARN_CUSTOMER", "GENERATE_REPORT", "ALLOW_TRANSACTION", "CLOSE_NO_FRAUD"]
AUTO = {"ALLOW_TRANSACTION", "MONITOR_CARD", "MONITOR_CONNECTED_CARDS", "WARN_CUSTOMER",
        "VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH", "GENERATE_REPORT", "CREATE_CASE",
        "ESCALATE_TO_ANALYST", "CLOSE_NO_FRAUD"}


def route(action: str, exposure: float) -> str:
    """Policy section 2, verbatim."""
    if action in AUTO:
        return "auto"
    if action == "DECLINE_TRANSACTION":
        return "L1"
    if action == "BLOCK_CARD":
        return "L1" if exposure <= C.BLOCK_L1_MAX else "L2"
    if action in ("BLOCK_ALL_CARDS", "FILE_REPORT"):
        return "L2"
    raise ValueError(f"unknown action {action}")


@dataclass
class PolicyInput:
    p: float                                  # assessed fraud probability
    verdict: str                              # fraud | legitimate | uncertain
    exposure: float
    pattern: str = "none"
    n_signals: int = 0                        # independent evidence axes supporting fraud
    trigger: str = "risk_score"
    response: str | None = None               # None | denied | confirmed | no_reply
    evidence_requested: bool = False
    request_type: str = ""                    # customer_validation | step_up_auth | analyst_info
    shared_origin: str = ""                   # named shared element (device / region / email)
    connected_cards: int = 0
    cross_customer: bool = False              # connects to another customer's fraud
    card_testing_cleared_max: float = 0.0     # largest purchase cleared after a testing burst
    recurring_match: bool = False             # R7
    conflicting: bool = False                 # R8 "the evidence conflicts"
    cards_confirmed_fraud: int = 0            # R10
    credentials_compromised: bool = False     # R10


@dataclass
class Decision:
    actions: list = field(default_factory=list)   # [{"action","route","reason"}]
    file_sar: bool = False
    sar_reason: str = ""
    notes: list = field(default_factory=list)

    def names(self):
        return [a["action"] for a in self.actions]


class _B:
    def __init__(self, exposure):
        self.exposure = exposure
        self.reasons: dict[str, list[str]] = {}
        self.vetoed: dict[str, str] = {}

    def add(self, action, reason):
        if action in self.vetoed:
            return
        self.reasons.setdefault(action, [])
        if reason not in self.reasons[action]:
            self.reasons[action].append(reason)

    def veto(self, action, reason):
        self.vetoed[action] = reason
        self.reasons.pop(action, None)

    def has(self, a):
        return a in self.reasons

    def build(self):
        out = []
        for a in ORDER:
            if a in self.reasons:
                out.append({"action": a, "route": route(a, self.exposure), "reason": "; ".join(self.reasons[a])})
        return out


def _money(x):
    return f"${x:,.2f}"


def decide(i: PolicyInput) -> Decision:
    b = _B(i.exposure)
    d = Decision()
    strongly = i.verdict == "fraud" or (i.p >= C.P_VERIFY_BELOW and i.verdict != "legitimate")
    weak = i.n_signals <= 1 and i.p < C.P_VERIFY_BELOW

    # ---------------- customer answered ----------------
    if i.response == "confirmed":
        b.add("CLOSE_NO_FRAUD", "R3: customer confirmed the transaction; confirmation noted in the case file")
        if i.evidence_requested or i.trigger == "customer_report":
            b.add("CREATE_CASE", "3a: case opened when evidence was requested; closed as legitimate under R3")
        if i.recurring_match:
            b.add("WARN_CUSTOMER", "R7: recurring-charge reminder so the customer recognises it next month")
    elif i.response == "denied":
        b.add("BLOCK_CARD", f"R2: customer denied the transaction; exposure {_money(i.exposure)} "
              f"{'<=' if i.exposure <= C.BLOCK_L1_MAX else '>'} $2,500")
        b.add("CREATE_CASE", "R2: customer denied the transaction")
        r2 = []
        if i.exposure > C.SAR_EXPOSURE:
            r2.append(f"exposure {_money(i.exposure)} exceeds $1,000")
        if i.shared_origin:
            r2.append(f"connects to shared {i.shared_origin}")
        if i.cross_customer:
            r2.append("connects to another card's fraud")
        if r2:
            b.add("FILE_REPORT", "R2: " + "; ".join(r2))
    elif i.response == "no_reply":
        b.add("MONITOR_CARD", "R4: no reply within 24 hours; monitoring raised for 72 hours")
        b.add("DECLINE_TRANSACTION", "R4: decline pending authorizations while the customer is unreachable")
        b.add("CREATE_CASE", "3a: evidence was requested, so a case is open")
        if i.exposure > C.ESCALATE_EXPOSURE:
            b.add("ESCALATE_TO_ANALYST", f"R4: no reply and exposure {_money(i.exposure)} exceeds $500")

    # ---------------- typology rules (apply before and after answers) ----------------
    if i.pattern == "card_testing" and i.response != "confirmed":
        b.add("DECLINE_TRANSACTION", "R5: three or more small online authorizations within an hour, then a larger purchase")
        b.add("STEP_UP_AUTH", "R5: require a one-time passcode before further activity")
        if i.card_testing_cleared_max > C.R5_CLEARED:
            b.add("BLOCK_CARD", f"R5: a purchase of {_money(i.card_testing_cleared_max)} (> $100) has already cleared")
        b.add("CREATE_CASE", "R5 / 3a: card-testing case")
    if i.shared_origin and i.connected_cards and strongly and i.response != "confirmed":
        b.add("CREATE_CASE", f"R6: several cards show fraud from the same {i.shared_origin}")
        b.add("FILE_REPORT", f"R6: shared origin named: {i.shared_origin}")
        b.add("MONITOR_CONNECTED_CARDS", f"R6: monitor the {i.connected_cards} other card(s) sharing the {i.shared_origin}")
    if i.pattern == "undocumented" and i.cross_customer and strongly and i.response != "confirmed":
        b.add("CREATE_CASE", "R9: coordinated abuse outside the five documented patterns")
        b.add("FILE_REPORT", "R9: undocumented pattern repeated across customers")
        b.add("ESCALATE_TO_ANALYST", "R9: undocumented pattern handed to an analyst with the evidence")
    if i.recurring_match and i.trigger == "customer_report" and i.response is None:
        b.add("CREATE_CASE", "R7: disputed charge matches the customer's own monthly recurring charge")
        b.add("VERIFY_WITH_CUSTOMER", "R7: confirm the recurring merchant with the customer")
        b.add("WARN_CUSTOMER", "R7: recurring-charge reminder")
        b.veto("BLOCK_CARD", "R7: do not block a disputed charge that matches a recurring pattern")
        b.veto("DECLINE_TRANSACTION", "R7")

    # ---------------- no answer yet: evidence strength ----------------
    if i.response is None and not i.recurring_match:
        if weak and i.p >= C.STOP_LOW:
            b.add("VERIFY_WITH_CUSTOMER", f"R1: probability {i.p:.2f} rests on a single signal; verify before any block")
            b.veto("BLOCK_CARD", "R1")
            b.veto("DECLINE_TRANSACTION", "R1")
        elif i.verdict == "fraud" and i.p >= C.P_VERIFY_BELOW and i.pattern != "card_testing":
            b.add("BLOCK_CARD", f"R1 satisfied (p={i.p:.2f} on {i.n_signals} independent signals) and 3b: "
                  f"exposure {_money(i.exposure)} {'<=' if i.exposure <= C.BLOCK_L1_MAX else '>'} $2,500")
            b.add("CREATE_CASE", f"3a: fraud probability {i.p:.2f} reaches 0.30")
        elif i.verdict != "legitimate" and C.STOP_LOW < i.p < C.STOP_HIGH and i.pattern != "card_testing":
            b.add("VERIFY_WITH_CUSTOMER", f"6: probability {i.p:.2f} is between 0.15 and 0.85; ask the customer")

    if i.response is None and i.evidence_requested and not i.recurring_match:
        if i.request_type == "step_up_auth":
            b.add("STEP_UP_AUTH", f"6/R1: probability {i.p:.2f} is not yet decisive; confirm the cardholder by one-time passcode")
        elif i.request_type == "customer_validation" and not b.has("VERIFY_WITH_CUSTOMER"):
            b.add("VERIFY_WITH_CUSTOMER", f"6: probability {i.p:.2f} is below 0.85; one customer answer settles it")

    # ---------------- R8 ----------------
    if i.verdict == "uncertain" and (i.exposure > C.ESCALATE_EXPOSURE or i.conflicting) and i.response != "confirmed":
        why = f"exposure {_money(i.exposure)} exceeds $500" if i.exposure > C.ESCALATE_EXPOSURE else "the evidence conflicts"
        b.add("ESCALATE_TO_ANALYST", f"R8: verdict uncertain and {why}")

    # ---------------- 3a: case ----------------
    if i.response != "confirmed":
        if i.p >= C.P_OPEN_CASE:
            b.add("CREATE_CASE", f"3a: fraud probability {i.p:.2f} reaches 0.30")
        if i.evidence_requested or b.has("VERIFY_WITH_CUSTOMER") or b.has("STEP_UP_AUTH"):
            b.add("CREATE_CASE", "3a: evidence requested")
        if i.trigger == "customer_report":
            b.add("CREATE_CASE", "3a: the customer disputes a charge")

    # ---------------- 3a: report ----------------
    sar = []
    if i.exposure > C.SAR_EXPOSURE:
        sar.append(f"exposure {_money(i.exposure)} exceeds $1,000")
    if i.shared_origin and i.connected_cards:
        sar.append(f"activity connects to a shared {i.shared_origin}")
    if i.cross_customer:
        sar.append("activity connects to another customer's fraud")
    if i.pattern == "undocumented" and i.cross_customer:
        sar.append("coordinated undocumented pattern (R9)")
    file_sar = strongly and bool(sar) and i.response != "confirmed" and i.verdict != "legitimate"
    if file_sar:
        b.add("FILE_REPORT", "3a: fraud " + ("confirmed" if i.verdict == "fraud" else "strongly suspected")
              + " and " + "; ".join(sar))
        b.add("CREATE_CASE", "3a: a report always has a case behind it")
        d.sar_reason = "3a" + ("/R2" if i.response == "denied" else "") + \
            ("/R6" if i.shared_origin and i.connected_cards else "") + \
            ("/R9" if i.pattern == "undocumented" and i.cross_customer else "") + ": fraud " + \
            ("confirmed" if i.verdict == "fraud" else "strongly suspected") + " and " + "; ".join(sar)
    else:
        if b.has("FILE_REPORT"):
            b.veto("FILE_REPORT", "3a conditions not met")
        if i.verdict == "legitimate" or i.response == "confirmed":
            d.sar_reason = "3a: no report — the activity was assessed as legitimate"
        elif not strongly:
            d.sar_reason = f"3a: no report — fraud is neither confirmed nor strongly suspected (p={i.p:.2f})"
        else:
            d.sar_reason = (f"3a: no report — exposure {_money(i.exposure)} is at most $1,000 and there is no shared "
                            "device, region cluster, other customer's fraud, or coordinated pattern; case only")

    # ---------------- R10 veto ----------------
    if not (i.cards_confirmed_fraud >= 2 or i.credentials_compromised):
        b.veto("BLOCK_ALL_CARDS", "R10")

    # ---------------- default ----------------
    if not b.reasons:
        if i.verdict == "legitimate" and i.p <= C.STOP_LOW:
            b.add("CLOSE_NO_FRAUD", f"6/3a: probability {i.p:.2f} at or below 0.15 on independent evidence; close as legitimate")
            b.add("ALLOW_TRANSACTION", "Policy 1: legitimate activity stands")
        else:
            b.add("MONITOR_CARD", f"Policy 1: probability {i.p:.2f}; raise monitoring for 72 hours")
    d.actions = b.build()
    d.file_sar = "FILE_REPORT" in d.names()
    for a in d.actions:
        assert a["reason"] and any(t in a["reason"] for t in ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9",
                                                             "R10", "3a", "3b", "6", "Policy")), a
    return d
