"""
Vector retrieval for GraphRAG (T5).

Embeds closed-case narratives into a fixed-dimension vector space and retrieves
the most similar prior cases by cosine similarity — semantic case memory that
works even when there is no entity overlap with the current investigation.

Design notes:
- Hashed TF-IDF (a.k.a. "the hashing trick"): deterministic, dependency-free,
  no external embedding API required, stable across runs (judging reproducibility).
- 128 dimensions, L2-normalised, IDF weighting fitted on the case corpus.
- The index is rebuilt once at startup from the in-memory graph; 5.5K cases
  embed in well under a second.

TigerGraph vector-storage note: when the Savanna build accepts TigerVector
VECTOR attributes, these same vectors can be persisted on ClosedCase vertices
and queried via knn_search (see scripts/t5_vector_probe.py). Until then the
vector store lives alongside the in-memory graph copy of the TG data.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from collections import Counter
from typing import Optional

from ..utils.models import ClosedCase, FraudPattern, InvestigationState

logger = logging.getLogger(__name__)

DIM = 128
_TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{2,}")

# Stopwords that carry no investigative signal
_STOP = {
    "the", "and", "was", "were", "has", "had", "for", "with", "this", "that",
    "from", "into", "after", "before", "been", "not", "but", "all", "any",
    "case", "cases", "transaction", "transactions", "customer", "card",
}


def tokenize(text: str) -> list[str]:
    toks = [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP]
    # add bigrams — "card_testing", "new_device" style signals live in pairs
    return toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]


def _hash(token: str) -> int:
    h = hashlib.md5(token.encode("utf-8")).digest()
    return int.from_bytes(h[:4], "little") % DIM


def _sign(token: str) -> int:
    h = hashlib.md5(token.encode("utf-8")).digest()
    return 1 if h[4] % 2 == 0 else -1


def embed(text: str, idf: Optional[dict[str, float]] = None) -> list[float]:
    """Deterministic hashed-TF-IDF embedding, L2-normalised."""
    vec = [0.0] * DIM
    counts = Counter(tokenize(text))
    if not counts:
        return vec
    max_tf = max(counts.values())
    for tok, tf in counts.items():
        weight = (tf / max_tf) * (idf.get(tok, 1.0) if idf else 1.0)
        vec[_hash(tok)] += weight * _sign(tok)
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def fit_idf(texts: list[str]) -> dict[str, float]:
    """IDF over the corpus; unseen tokens default to 1.0 in embed()."""
    n = max(1, len(texts))
    df: Counter = Counter()
    for t in texts:
        df.update(set(tokenize(t)))
    return {tok: math.log((1 + n) / (1 + c)) + 1.0 for tok, c in df.items()}


def case_text(case: ClosedCase) -> str:
    """Flatten a closed case into the text that gets embedded."""
    parts = [
        f"pattern {case.pattern}",
        f"outcome {case.outcome}",
        f"exposure_band {'large' if case.exposure_usd >= 500 else 'medium' if case.exposure_usd >= 100 else 'small'}",
        f"txns {case.n_txns}",
        case.analyst_notes or "",
        case.actions_taken or "",
    ]
    return " ".join(parts)


def investigation_text(state: InvestigationState) -> str:
    """Flatten the current investigation into the same vector space."""
    parts = []
    reason = getattr(state.trigger, "trigger_text", "") or getattr(state.trigger, "reason", "")
    if reason:
        parts.append(f"trigger {reason}")
    if state.pattern != FraudPattern.NONE:
        parts.append(f"pattern {state.pattern.value}")
    if state.flagged_txn is not None:
        amt = state.flagged_txn.amount
        parts.append(
            f"exposure_band {'large' if amt >= 500 else 'medium' if amt >= 100 else 'small'}")
    if state.device_neighbors:
        parts.append("device shared by cards new_device")
    for ev in state.evidence_collected:
        parts.append(ev.claim)
    return " ".join(parts)


class CaseVectorIndex:
    """Vector index over closed cases; cosine top-k retrieval."""

    def __init__(self, cases: list[ClosedCase]):
        self._texts = {c.case_id: case_text(c) for c in cases}
        self._idf = fit_idf(list(self._texts.values()))
        self._vectors = {
            cid: embed(text, self._idf) for cid, text in self._texts.items()
        }
        self._by_id = {c.case_id: c for c in cases}
        logger.info("CaseVectorIndex: embedded %d closed cases into %d-dim space",
                    len(self._vectors), DIM)

    def search(self, query: str, k: int = 4,
               min_score: float = 0.05) -> list[tuple[ClosedCase, float]]:
        qv = embed(query, self._idf)
        scored = [
            (self._by_id[cid], cosine(qv, vec))
            for cid, vec in self._vectors.items()
        ]
        scored = [s for s in scored if s[1] >= min_score]
        scored.sort(key=lambda s: s[1], reverse=True)
        return scored[:k]

    def __len__(self) -> int:
        return len(self._vectors)
