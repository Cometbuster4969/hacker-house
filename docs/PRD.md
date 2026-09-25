# PRD — Agentic Fraud Investigation on TigerGraph

Product requirements, traceability and accepted gaps. The source requirements are the challenge brief
([`BRIEF.md`](BRIEF.md)) and the dataset's own `HHGOA_IEEE/README.md` (answer format + **Fraud Policy v1.0**).

Every row names the file that satisfies it. A row is only marked ✅ when a command in
[`../README.md`](../README.md) reproduces it.

---

## 1. Product thesis

> The graph supplies the facts. A model learned from the bank's own closed cases supplies the
> probability. A pure policy engine makes the call. The LLM is optional, and never writes an action.

Four layers with hard boundaries, enforced by module structure rather than by discipline:

| Layer | Owns | Nature | Where |
|---|---|---|---|
| L1 Evidence | `InvestigationStore` (mirror of TigerGraph) + 9 GSQL queries | deterministic, citeable | `src/engine/store.py`, `tigergraph/queries/engine_queries.gsql` |
| L2 Judgement | gradient-boosted txn model, pattern classifier, logistic calibrator | probabilistic | `src/engine/model.py`, `patterns.py`, `calibration.py` |
| L3 Decision | `policy.decide()` — R1–R10, 3a, 3b, §2 routes | deterministic, auditable, pure | `src/engine/policy.py` |
| L4 Explanation | template rendering from structured findings | generative-free, traceable | `src/engine/narrative.py` |

**Invariants.** L3 is the only writer of `next_best_actions` and `sar.file`. L1 is the only reader of
transaction rows. L4 may not introduce an entity id that L1 did not return. The agent loop
(`investigator.py`) selects tools; it cannot author a verdict.

---

## 2. Brief requirements → implementation

### 2.1 The ten agent requirements (brief §"Core Challenge")

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Investigate on 3 trigger types (risk score, customer report, analyst request) | ✅ | all 3 in the pack (11 / 8 / 1 of 20): `data/HHGOA_IEEE/case_pack.csv`, `investigator.py` intake |
| 2 | Gather evidence from graph, txn history, device/identity, behaviour, prior cases, external | ✅ | 9 as-of-bounded queries; `evidence[].source` ∈ {graph, document, customer, external} |
| 3 | Identify and name the fraud pattern | ✅ | `patterns.py` (5 documented + `undocumented`); replay pattern accuracy 0.769 |
| 4 | Create and progress a case with status, risk, actions, decision record | ✅ | `cases/HHG-*.json` `case.status`, `next_best_actions.{initial,final,what_changed}` |
| 5 | Case memory that informs later cases and updates on resolution | ✅ | `memory.py` (graph hops + behavioural kNN over 5,565 closed cases + the agent's own cases); `memory/agent_cases.json` |
| 6 | Gather more evidence through policy-approved requests | ✅ | `evidence_requests[]` with `asked_after_step` + simulated reply; request rate 0.763 in replay |
| 7 | Recommend next actions | ✅ | `next_best_actions` — exact policy action names, validator-checked |
| 8 | Operate inside policies/permissions; approvals required for non-`auto` | ✅ | `policy.route()`; every action carries `auto`/`L1`/`L2`, route mismatch = violation |
| 9 | Stop when evidence is enough | ✅ | `stop_reason` per case; thresholds 0.85 / 0.15 and `MAX_EVIDENCE_ROUNDS=2` in `engine/config.py` |
| 10 | Explain reasoning: evidence used, why more was asked, why these actions | ✅ | `case.summary` (≤6 sentences, enforced), `what_changed`, per-action `reason` citing a rule |

### 2.2 Required components (brief §"What Participants Build With")

| Component | Status | Evidence |
|---|---|---|
| TigerGraph Savanna / CE for graph + vector storage | ⚠️ graph ✅, vector ❌ | 8 vertex types / 18 edge types live (`evidence/tg_live_check.json`), 20/20 `InvestigationCase` written. TigerVector: `add_vector_attribute` **failed** on the deployed instance — see §5 |
| GSQL + graph algorithms | ✅ | 9 installed engine queries; earlier session: 10/10 GSQL checks incl. `fraud_pagerank` (GDS) in `evidence/legacy_run/tigergraph_query_results.json` |
| TigerGraph MCP | ✅ | `tigergraph-mcp` exercised over stdio: 5/5 checks, 69 tools exposed — `evidence/legacy_run/mcp_tool_verification.json`; our own tool server `src/mcp/engine_server.py` (7 tools) |
| GraphRAG grounding | ✅ | `memory.py`: multi-hop graph retrieval → closed cases, plus policy/typology document chunks cited as `source: document` |
| User interface | ✅ | `src/ui/app.py`: 7 endpoints (cases, case, trace, benchmark, monitoring, graph stats) on `:8000` |
| Agent framework (optional) | ✅ | custom loop in `investigator.py`; LangGraph not required for the headline path |
| LLM for reasoning/tool selection (optional) | ⚠️ | engine renders narrative from findings: **0 tokens**. LLM orchestrator kept and runnable: `python main.py investigate --legacy` |
| External/simulated interactions (optional) | ✅ | customer replies simulated under a disclosed fixed policy, recorded in `evidence_requests[].assumed_response` |

### 2.3 Fraud Policy v1.0 → code

Rule ids are quoted in every action `reason` and checked by `RULE` in `src/engine/validate.py`.

| Rule | Encoded in | Pinned by |
|---|---|---|
| R1 verify before blocking on a weak single signal (<0.70) | `policy.py` (blocking gate + `n_signals`) | `tests/test_policy.py`, validator `R1: block below 0.70 without verification` |
| R2 customer denies → BLOCK_CARD + CREATE_CASE (+FILE_REPORT if >$1,000 or shared device) | `policy.py` response branch | `test_policy.py` |
| R3 customer confirms → CLOSE_NO_FRAUD | `policy.py` | `test_policy.py` |
| R4 no reply → MONITOR_CARD + DECLINE_TRANSACTION, escalate >$500 | `policy.py` | `test_policy.py` |
| R5 card testing (≥3 micro-auths in an hour + larger purchase; >$100 cleared → block) | `patterns.py`, `policy.py` | `test_policy.py`; monitor opens R5 only with model corroboration |
| R6 shared origin → name it, CREATE_CASE, FILE_REPORT, MONITOR_CONNECTED_CARDS | `rings.py`, `policy.py` | validator `MONITOR_CONNECTED_CARDS without connected cards` |
| R7 disputed-but-recurring → CREATE_CASE + VERIFY + WARN, never block | `patterns.py` `recurring_check`, `policy.py` | validator `legitimate verdict with adverse actions` |
| R8 escalate when uncertain and exposed >$500 or evidence conflicts | `policy.py` | validator `R8: ... without escalation` |
| R9 undocumented pattern → CREATE_CASE + FILE_REPORT + ESCALATE + own words | `policy.py`; `pattern_description` required | validator `undocumented pattern needs a 2-3 sentence description` |
| R10 never BLOCK_ALL_CARDS unless ≥2 cards confirmed / credentials confirmed | `policy.py` veto | validator: BLOCK_ALL_CARDS must not appear in these 20 cases |
| 3a case ≠ report (open at ≥0.30; report only on the stated triggers) | `policy.py` + `SAR_EXPOSURE` | validator `sar.file disagrees with FILE_REPORT`, `3a: p>=0.30 without CREATE_CASE` |
| 3b initial vs final action, and what changed | `investigator.py` second `decide()` | validator `no evidence requested but final != initial` |
| §2 approval routes auto / L1 / L2 (L2 for FILE_REPORT, >$2,500, BLOCK_ALL_CARDS) | `policy.route()` | validator recomputes the route for every action |

### 2.4 Answer-file contract

`src/engine/validate.py` implements **58 mechanical checks** over the format in the dataset README
(15 required `case` keys, evidence-item shape, action vocabulary, SAR narrative 6–12 sentences,
exposure = Σ|amt|, earliest-txn ordering, id existence, status/verdict/probability consistency,
summary length). `python main.py validate` runs tier A with no dataset: **20 files, 0 violations**.

---

## 3. Non-goals (chosen, not forgotten)

| Cut | Why | Cost |
|---|---|---|
| No fine-tuning, no RAG-over-transactions prompt stuffing | every claim must resolve to an id the graph returned | less "impressive" prose |
| No LLM in the decision path | a sampled action cannot be unit-tested; a quarter of the score is legal actions | weaker free-text demo |
| No streaming/async serving, no auth, no multi-tenant UI | 20-case benchmark, one analyst | not a product |
| No `BLOCK_ALL_CARDS` firing | R10's precondition is not met anywhere in the data | rule still implemented + vetoed |

---

## 4. Quality bar

| Gate | Command | Result |
|---|---|---|
| Contract + policy | `python main.py validate` | 20 files, 0 violations |
| Unit/integration | `python -m pytest -q` | **87 passed, 2 skipped** on `.[dev]` alone (the two skips want the dataset store and the `tigergraph` extra) → **89 passed, 1 skipped** on `.[all]` |
| Metric claims | `python main.py train` / `backtest` | regenerate `benchmark/*.json` quoted in the README |
| No leakage | `train_models.py` splits Jul–Aug train / Sep–Oct test; `case_backtest.py` trains from Jul–Aug only | holds by construction, stated in `docs/RESULTS.md` |
| Reproducibility from clean clone | `pip install -e ".[dev]"` | works with no dataset and no TigerGraph |

---

## 5. Known gaps (what we would fix first)

1. **TigerVector not deployed.** `add_vector_attribute` was rejected by the live instance, so
   behavioural retrieval runs on the local index (`models/case_index.parquet`,
   `scripts/build_memory_index.py`). The graph path returns the same neighbours; the vector path is
   not yet the retrieval engine.
2. **Tier-B validation needs the dataset.** id existence and exposure re-summation only run after
   `main.py build`. Tier A catches contract/policy/route/SAR violations, not unknown-ids.
3. **Customer replies are simulated**, not a real contact channel — disclosed in every
   `assumed_response`.
4. **`V`, `C`, `D`, `M` and the numeric `id` column are undefined** in Vesta's release; they are used
   as signals and described as such, never as "known fraud indicators".
5. **`addr1` is a billing region code, not an address** — region-based reasoning (R2/typology 4) uses
   it as a history-divergence signal only.
6. **Case-pack prior is measured on a 50/50 benchmark**, so calibration is prior-shifted for the
   benchmark and reported separately on the natural-population replay
   (`fraud_recall 0.65`, `legit_recall 0.82`, 10.5% left uncertain).
