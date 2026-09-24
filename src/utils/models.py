"""Core data models for the fraud investigation system."""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class TriggerType(str, Enum):
    RISK_SCORE = "risk_score"
    CUSTOMER_REPORT = "customer_report"
    ANALYST_REQUEST = "analyst_request"


class CaseStatus(str, Enum):
    OPEN = "open"
    CLOSED_FRAUD = "closed_fraud"
    CLOSED_LEGITIMATE = "closed_legitimate"
    ESCALATED = "escalated"


class Verdict(str, Enum):
    FRAUD = "fraud"
    LEGITIMATE = "legitimate"
    UNCERTAIN = "uncertain"


class FraudPattern(str, Enum):
    CARD_TESTING = "card_testing"
    CARD_NOT_PRESENT_FRAUD = "card_not_present_fraud"
    CARD_NOT_PRESENT_NEW_DEVICE = "card_not_present_new_device"
    OUT_OF_REGION_USE = "out_of_region_use"
    ACCOUNT_TAKEOVER = "account_takeover"
    UNDOCUMENTED = "undocumented"
    NONE = "none"


class SourceType(str, Enum):
    GRAPH = "graph"
    DOCUMENT = "document"
    CUSTOMER = "customer"
    EXTERNAL = "external"


class ApprovalRoute(str, Enum):
    AUTO = "auto"
    L1 = "L1"
    L2 = "L2"


class ActionType(str, Enum):
    ALLOW_TRANSACTION = "ALLOW_TRANSACTION"
    DECLINE_TRANSACTION = "DECLINE_TRANSACTION"
    MONITOR_CARD = "MONITOR_CARD"
    MONITOR_CONNECTED_CARDS = "MONITOR_CONNECTED_CARDS"
    WARN_CUSTOMER = "WARN_CUSTOMER"
    VERIFY_WITH_CUSTOMER = "VERIFY_WITH_CUSTOMER"
    STEP_UP_AUTH = "STEP_UP_AUTH"
    BLOCK_CARD = "BLOCK_CARD"
    BLOCK_ALL_CARDS = "BLOCK_ALL_CARDS"
    GENERATE_REPORT = "GENERATE_REPORT"
    CREATE_CASE = "CREATE_CASE"
    FILE_REPORT = "FILE_REPORT"
    ESCALATE_TO_ANALYST = "ESCALATE_TO_ANALYST"
    CLOSE_NO_FRAUD = "CLOSE_NO_FRAUD"


# --- Transaction & Identity ---

class Transaction(BaseModel):
    transaction_id: str
    customer_id: str
    card_id: str
    amount: float
    timestamp: str  # YYYY-MM-DD HH:MM:SS
    channel: str  # in_person | online
    risk_score: float
    product_cd: str = ""
    card1: str = ""
    card2: str = ""
    card3: float = 0.0
    card4: str = ""  # network
    card5: float = 0.0
    card6: str = ""  # type
    addr1: float = 0.0  # billing region
    addr2: float = 0.0  # country code
    dist1: float = 0.0
    dist2: float = 0.0
    p_emaildomain: str = ""
    r_emaildomain: str = ""
    raw_data: dict = Field(default_factory=dict)


class IdentityRecord(BaseModel):
    transaction_id: str
    device_type: str = ""
    device_info: str = ""
    id_01: float = 0.0
    id_02: float = 0.0
    id_03: float = 0.0
    id_04: float = 0.0
    id_05: float = 0.0
    id_06: float = 0.0
    id_07: float = 0.0
    id_08: float = 0.0
    id_09: float = 0.0
    id_10: float = 0.0
    id_11: float = 0.0
    id_12: str = ""
    id_13: str = ""
    id_14: str = ""
    id_15: str = ""  # New / Found
    id_16: str = ""
    id_17: str = ""
    id_18: str = ""
    id_19: str = ""
    id_20: str = ""
    id_21: str = ""
    id_22: str = ""
    id_23: str = ""  # proxy type
    id_24: str = ""
    id_25: str = ""
    id_26: str = ""
    id_27: str = ""
    id_28: str = ""
    id_29: str = ""
    id_30: str = ""  # OS
    id_31: str = ""  # browser
    id_32: str = ""
    id_33: str = ""  # screen
    id_34: str = ""  # match status
    id_35: str = ""
    id_36: str = ""
    id_37: str = ""
    id_38: str = ""
    raw_data: dict = Field(default_factory=dict)

    @property
    def device_profile_key(self) -> str:
        """Composite key: DeviceInfo | id_30 | id_31 | id_33"""
        parts = [
            self.device_info or "UNK",
            self.id_30 or "UNK",
            self.id_31 or "UNK",
            self.id_33 or "UNK",
        ]
        return " | ".join(parts)


# --- Closed Cases ---

class ClosedCase(BaseModel):
    case_id: str
    customer_id: str
    card_id: str
    opened_at: str
    closed_at: str
    outcome: str  # confirmed_fraud | cleared
    pattern: str
    first_fraud_txn_id: str = ""
    txn_ids: list[str] = Field(default_factory=list)
    n_txns: int = 0
    exposure_usd: float = 0.0
    connected_card_ids: list[str] = Field(default_factory=list)
    actions_taken: str = ""
    report_filed: bool = False
    analyst_notes: str = ""


# --- Case Pack (Benchmark Cases) ---

class CasePackEntry(BaseModel):
    case_id: str
    opened_at: str
    trigger_type: TriggerType
    trigger_text: str
    flagged_txn_id: str
    card_id: str
    customer_id: str
    risk_score: Optional[float] = None


# --- Evidence ---

class Evidence(BaseModel):
    claim: str
    source: SourceType
    ref: str
    entity_ids: list[str] = Field(default_factory=list)


class EvidenceRequest(BaseModel):
    type: str  # customer_validation | step_up_auth | analyst_info
    asked_after_step: int
    assumed_response: str = ""


# --- Next Best Action ---

class ActionRecommendation(BaseModel):
    action: ActionType
    route: ApprovalRoute
    reason: str


class NextBestActions(BaseModel):
    initial: list[ActionRecommendation] = Field(default_factory=list)
    final: list[ActionRecommendation] = Field(default_factory=list)
    what_changed: str = "nothing"


# -- SAR ---

class SuspiciousActivityReport(BaseModel):
    file: bool = False
    reason: str = ""
    narrative: str = ""
    subjects: list[str] = Field(default_factory=list)
    total_amount_usd: float = 0.0
    activity_dates: list[str] = Field(default_factory=list)


# --- Investigation Case ---

class InvestigationCase(BaseModel):
    status: CaseStatus = CaseStatus.OPEN
    verdict: Verdict = Verdict.UNCERTAIN
    fraud_probability: float = 0.5
    pattern: FraudPattern = FraudPattern.NONE
    pattern_description: str = ""
    affected_txn_ids: list[str] = Field(default_factory=list)
    first_suspicious_txn_id: str = ""
    connected_card_ids: list[str] = Field(default_factory=list)
    connected_device_profiles: list[str] = Field(default_factory=list)
    exposure_usd: float = 0.0
    evidence: list[Evidence] = Field(default_factory=list)
    similar_prior_cases: list[str] = Field(default_factory=list)
    summary: str = ""
    written_to_graph: bool = False
    graph_case_id: str = ""


# --- Full Answer ---

class CaseAnswer(BaseModel):
    case_id: str
    case: InvestigationCase
    evidence_requests: list[EvidenceRequest] = Field(default_factory=list)
    next_best_actions: NextBestActions = Field(default_factory=NextBestActions)
    sar: SuspiciousActivityReport = Field(default_factory=SuspiciousActivityReport)
    stop_reason: str = ""
    tool_calls: int = 0
    tokens: int = 0
    latency_s: float = 0.0


# --- Agent State ---

class InvestigationState(BaseModel):
    """Tracks the state of an ongoing investigation."""
    case_id: str
    trigger: CasePackEntry
    flagged_txn: Optional[Transaction] = None
    flagged_identity: Optional[IdentityRecord] = None
    card_transactions: list[Transaction] = Field(default_factory=list)
    customer_transactions: list[Transaction] = Field(default_factory=list)
    connected_transactions: list[Transaction] = Field(default_factory=list)
    device_neighbors: list[dict] = Field(default_factory=list)
    region_neighbors: list[dict] = Field(default_factory=list)
    email_neighbors: list[dict] = Field(default_factory=list)
    similar_closed_cases: list[ClosedCase] = Field(default_factory=list)
    identity_records: dict[str, IdentityRecord] = Field(default_factory=dict)
    connected_card_ids: list[str] = Field(default_factory=list)
    evidence_collected: list[Evidence] = Field(default_factory=list)
    evidence_requests: list[EvidenceRequest] = Field(default_factory=list)
    fraud_probability: float = 0.5
    verdict: Verdict = Verdict.UNCERTAIN
    pattern: FraudPattern = FraudPattern.NONE
    pattern_description: str = ""
    steps_completed: int = 0
    tool_calls: int = 0
    graph_writes: list[dict] = Field(default_factory=list)
