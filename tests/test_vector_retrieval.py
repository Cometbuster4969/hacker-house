"""T5 — vector retrieval tests: determinism, normalisation, ranking, scale."""
import time

from src.evidence.vector_retrieval import embed, CaseVectorIndex, fit_idf
from src.utils.models import ClosedCase


def _case(cid, outcome, pattern, notes, exposure=120.0, n=4):
    return ClosedCase(
        case_id=cid, customer_id=f"C-{cid}", card_id=f"K-{cid}",
        opened_at="2025-01-01", closed_at="2025-01-02",
        outcome=outcome, pattern=pattern, analyst_notes=notes,
        exposure_usd=exposure, n_txns=n,
    )


CASES = [
    _case("CC-1", "confirmed_fraud", "card_testing",
          "three small online authorizations then larger purchase, new device"),
    _case("CC-2", "cleared", "none",
          "customer confirmed transaction, authorised purchase while travelling",
          exposure=40.0, n=1),
    _case("CC-3", "confirmed_fraud", "account_takeover",
          "new device, email change, rapid balance drain", exposure=900.0, n=6),
]


def test_embedding_deterministic_and_normalised():
    a = embed("card_testing small exposure new device")
    b = embed("card_testing small exposure new device")
    assert a == b
    assert abs(sum(x * x for x in a) - 1.0) < 1e-9


def test_retrieval_ranks_matching_pattern_first():
    idx = CaseVectorIndex(CASES)
    hits = idx.search(
        "pattern card_testing exposure_band medium device shared by cards "
        "new_device three small authorizations", k=2)
    assert hits, "expected at least one hit"
    assert hits[0][0].case_id == "CC-1"
    assert hits[0][1] > hits[-1][1]


def test_cleared_case_outranks_fraud_for_benign_query():
    idx = CaseVectorIndex(CASES)
    hits = idx.search(
        "customer confirmed transaction made authorised travelling", k=3)
    assert hits[0][0].case_id == "CC-2"


def test_scale_5565_cases():
    import random
    random.seed(7)
    words = ["fraud", "testing", "takeover", "device", "denied",
             "cleared", "confirmed", "phishing", "small", "drain"]
    big = [
        _case(f"CC-{i}", random.choice(["confirmed_fraud", "cleared"]), "p",
              " ".join(random.choices(words, k=30)))
        for i in range(5565)
    ]
    t0 = time.time()
    idx = CaseVectorIndex(big)
    build_s = time.time() - t0
    t0 = time.time()
    hits = idx.search("fraud testing device denied", k=4)
    query_s = time.time() - t0
    assert len(hits) == 4
    assert build_s < 10.0, f"index build too slow: {build_s:.2f}s"
    assert query_s < 1.0, f"query too slow: {query_s:.3f}s"


def test_idf_fitting_downweights_common_tokens():
    texts = ["fraud device fraud device", "fraud device quiet", "fraud device calm"]
    idf = fit_idf(texts)
    assert idf["fraud"] < idf["quiet"]