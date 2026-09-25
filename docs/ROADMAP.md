# ROADMAP — how this solution was built, in order

Kept as a build log rather than a plan: each phase names what was believed going in, what broke, what
shipped and where it lives. Useful for judging process, and the honest place to see why v1 was dropped.

`P0` is the starting point; `P9` is the state of `main`.

---

## P0 — Read the data before writing an agent

* Dataset README read end to end: answer format, Fraud Policy v1.0, the five typologies, §2 routes.
* Measured the corpus before choosing features: 590,742 transactions, 144,432 identity rows,
  5,565 closed cases, **4,665 confirmed fraud**; within-case txn gap distribution → p99 = 46 h, which
  is where `EPISODE_GAP_H = 48.0` comes from (`src/engine/config.py`).
* Found the seeding artefact: every seeded row has `seconds = 0` in its timestamp. Decided the agent
  must never key on it — detectors are behavioural (amounts, timing spread, device anomaly,
  cross-customer spread). Documented in the README's provenance section.

**Deliverable:** `tigergraph/schema.gsql` (8 vertices, 18 edges) designed around questions, not tables:
`NEXT_TXN` for bursts, `SHARES_DEVICE` for rings, `CASE_SIMILAR_TO` for memory.

## P1 — v1: rules + LLM orchestrator (later dropped from the decision path)

* `src/agent/orchestrator.py` + `llm_reasoner.py`: LLM picks tools, reads the policy text, writes actions.
* Result on the 20 cases: **17 fraud / 1 legitimate / 2 uncertain, 14 SARs, 211,956 tokens, 802.7 s**
  (`evidence/legacy_run/run_summary.json`). Mean probability 0.773.
* Failure mode named in one line: *the model was asked for a verdict and obliged.* On a 50/50 pack,
  17/20 fraud is a recall disaster in the direction that costs the bank customers.
* Live TigerGraph work in this phase did land and is kept: schema deployed, 10/10 GSQL smoke queries
  (including `fraud_pagerank` via GDS), `tigergraph-mcp` 5/5 checks over 69 tools
  (`evidence/legacy_run/`).

**Decision:** keep the graph plumbing, throw the decision path away.

## P2 — Store, not prompts

* `src/engine/store.py`: parquet store, every read `as_of`-bounded, `id_set/card_set/customer_set`
  exposed so the validator can check ids exist. `_check_ids()` hard-fails the build unless all 20
  case-pack `card_id`s match the transactions — a data-integrity gate, not a test.
* Mirror of every store read in GSQL: `tigergraph/queries/engine_queries.gsql` (9 queries), so the
  local engine and a live graph return the same rows.

## P3 — Learn probability from closed cases only

* Labels come only from `closed_cases_history.csv` outcomes — the only confirmed ground truth in the
  release. Train Jul–Aug, test Sep–Oct: **ROC-AUC 0.9466** vs **0.861** for the bank's risk score,
  Brier 0.018 (`benchmark/backtest.json`).
* Prior-shifted calibrator: the closed-case corpus is 84% fraud, the alert population is ~50%, so a raw
  posterior is wrong in the first decimal. `calibration.py` fits on replayed closed cases then shifts.
* v1's over-flagging was fixed here, not by prompting.

## P4 — Make the policy a pure function

* `policy.decide(PolicyInput) -> Decision`: R1–R10, 3a, 3b, §2 routes, and the R10 veto. No I/O, no
  clock, no model call, no string parsing.
* Pinned by `tests/test_policy.py` (rule-by-rule) *and* by the answer-file validator, which recomputes
  `route()` for every action in both stages.
* The LLM is now structurally unable to emit an action: only `policy.decide` returns actions.

## P5 — Hypothesis-driven tool loop

* `investigator.py`: intake → `txn_context` → `cardholder_baseline` → `card_window` + scoring → tools
  chosen by what the evidence actually suggests (`structuring_scan` → population sweep,
  `device_neighbors` → ring check, `card_testing_scan`, `recurring_check`) → `assessment` → `decide()`.
* Every step records why the tool was called → `traces/HHG-*.json`. 181 tool calls across 20 cases,
  8.1 mean per case in replay.
* Value-of-information gate before asking the customer, so asking is a decision and not a reflex
  (request rate 0.763 on replay; 13 of the 20 benchmark cases ask, and all 13 are the
  legitimate/uncertain ones — the agent asks when it is *not* sure, never to confirm what it already
  believes).

## P6 — Two typologies the dataset does not document

* **Sub-$500 structuring (HHG-006)** — 4 online purchases $456.96–$488.04 in 30 minutes; the same burst
  shape appears on 3 other cards in the prior 30 days → R9.
* **Shared anonymous-proxy device ring (HHG-014)** — one `SM-G935F` profile, marked New, behind an
  anonymous proxy, on 20 cards of 20 customers in 30 days → R6 + R9, 19 connected cards monitored.
* Rings are built only through hardware-specific or anomalous device profiles (`rings.py`), otherwise
  generic `Windows | chrome` profiles fabricate thousand-card rings. This is the bug class that broke
  the prototype this repo merged: 19 of its 20 files listed ~2,100 "connected" cards on every case.

## P7 — Prove it on the bank's own history, not on the 20 cases

* `scripts/backtest_closed_cases.py`: replay 544 October closed cases, models from Jul–Aug, calibrator
  from Sep, each alert presented as a plain risk-score alert (a *random* txn of the episode, which is
  harder than the real trigger).
* Result: verdict accuracy **0.8185** on decided cases, **10.48%** left uncertain, episode Jaccard
  **0.8218**, pattern accuracy **0.7692**, SAR agreement **0.91**, blocks on cleared cases **8.33%**
  vs **64.5%** of confirmed cases blocked (`benchmark/case_backtest.json`).
* Disclosed weakness kept in the open rather than tuned away: `fraud_recall 0.65`.

## P8 — Go beyond the case pack

* `scripts/monitor.py`: sweep Nov–Dec with no case pack, open alerts by anomaly, investigate with the
  same engine/policy pair → 15 alerts: 12 sub-$500 structuring bursts, the SM-G935F ring
  (28 customers, 60 txns, includes HHG-014's card), 1 thin candidate ring (escalated for a human),
  1 R5 card-testing sequence (opened only with model corroboration).

## P9 — Merge, then make the whole thing verifiable from a clean clone

* Merged the second prototype's policy/state design and rebuilt around `src/engine/`.
* Added `pyproject.toml` (`pip install -e ".[dev]"`, `hhgoa` console script), split validation into
  tier A (no dataset) / tier B (ids + exposure re-derived), reconciled the README with the artifacts
  (`written_to_graph` is `true` 20/20 with `evidence/tg_live_check.json` behind it), and recorded a
  narrated walkthrough under `docs/demo/`.

## P10 — Demo, then trimmed to the submission set

* Dropped the scratch surface that had no reader: one-off patchers (`apply_fixes.py`, `fix_check.py`),
  the superseded query installer (`final_queries.py`) and the LLM smoke check (`check_llm.py`), the
  157-file legacy LLM response cache (now git-ignored - it is a cache), the archive of the merged
  prototype, and the intermediate demo media (figures, slide frames, narration clips) once the
  walkthrough itself was rendered. `docs/demo/` keeps the video and the dashboard snapshot.
* `main.py` no longer imports the web stack or the MCP server at module level, so
  `python main.py validate` runs on the minimal `.[dev]` install - which is what the README promises
  and what `.github/workflows/ci.yml` now enforces in two jobs (`.[dev]` minimal, `.[all]` full plus a
  dashboard smoke test over 6 endpoints).
* `main.py serve --port N` actually honoured `N` again (it read the config default and ignored the
  flag) - found by running the smoke test the CI file now runs: six endpoints, all `200`.
* Kept deliberately: `scripts/verify_t6_mcp.py`, `scripts/deploy_schema.py` and
  `scripts/load_to_tigergraph.py`, because they are how `evidence/` was produced - removing them would
  leave checked-in proof with no way to regenerate it.

### Deferred

| Item | Why deferred |
|---|---|
| TigerVector as the retrieval engine | `add_vector_attribute` rejected by the deployed instance; local index gives the same neighbours |
| Real customer channel | out of scope; simulated replies are disclosed |
| Merchant-level reasoning | no merchant column; `ProductCode` is the proxy for R7 and is labelled as such |
| Graph algorithms in the live path | `ring_components` walk exists in the earlier session; the engine does the equivalent traversal locally so results are unit-testable |
