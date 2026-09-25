# 🕵️ Agentic Fraud Investigation — TigerGraph + a learned, policy-bound agent

**HHGOA 2026 — TigerGraph Partner Challenge (Agentic Fraud Investigation)**

> Two prototypes — a rules+LLM investigation agent and a pure policy-engine design — were merged into
> a single evidence-first engine (`src/engine/`) and re-checked against a leakage-free backtest on the
> bank's own closed cases. Every headline number in this README is reproduced by the commands under
> [Quick start](#quick-start); nothing is quoted from a run that is not in the repo.

The agent takes an alert (a risk score, a customer report or an analyst request), investigates the
bank's transaction graph *as of the moment the case was opened*, reconstructs the fraud episode,
names the pattern (including two **undocumented** typologies it found itself), retrieves precedents
from the bank's closed cases, decides whether a customer answer is worth asking for, and hands the
case state to a **pure policy engine**. That engine is the only thing that writes
`next_best_actions` and `sar.file`.

## Results

### The 20 benchmark cases (`cases/HHG-*.json`, 0 contract/policy violations)

| Verdict | Cases |
|---|---|
| **fraud (8)** | 006 structuring ring · 007 · 008 · 009 · 014 device ring · 016 · 018 · 019 |
| **legitimate (9)** | 001 · 002 · 005 · 010 · 012 · 013 · 015 · 017 · 020 |
| **uncertain (3)** | 003 (no reply) · 004, 011 (customer insists, evidence says ordinary → escalated, R8) |

- 3 SARs (006 R9 structuring, 014 R6/R9 device ring, 018 exposure $2,752 > $1,000). The other fraud cases are **case only** (3a).
- Two undocumented patterns, both found by the agent's own sweeps rather than forced into a known label:
  - **Sub-$500 structuring (HHG-006):** 4 online purchases between $456.96 and $488.04 in 30 minutes. The same burst shape appears on 3 other customers' cards in the prior 30 days.
  - **Shared anonymous-proxy device ring (HHG-014):** one Samsung SM-G935F profile, marked New and behind an anonymous proxy, used on 20 cards of 20 customers in 30 days. The agent puts 19 connected cards under monitoring.
- Per-case table: [`benchmark/run_summary.json`](benchmark/run_summary.json). Step-by-step agent traces: [`traces/`](traces/).

### Honest backtest (no leakage, [`benchmark/`](benchmark/))

| What | Result |
|---|---|
| Transaction model, Sep–Oct holdout (trained Jul–Aug) | **ROC-AUC 0.947** vs 0.861 for the bank's risk score |
| Full agent replay, 544 October closed cases (models from Jul–Aug, calibrator from Sep, every alert shown as a plain risk-score alert) | verdict accuracy **0.82** on decided cases (50/50 weighting), 10% left uncertain |
| Blocks on cases the bank cleared | **8.3%** (vs 64.5% of confirmed cases blocked) |
| Episode reconstruction | mean Jaccard **0.82**, exact match 63% |
| Pattern accuracy on fraud verdicts | **0.77** |
| SAR decision agreement with the bank's analysts | **0.91** |
| Answer files vs the contract | 58 mechanical checks, **20 files / 0 violations**, runnable on a clean clone (`python main.py validate`) |
| Unit + integration tests | **87 passed, 2 skipped** on `pip install -e ".[dev]"` alone (skips need the dataset store / the `tigergraph` extra) → **89 passed, 1 skipped** on `".[all]"`. `python -m pytest -q`, no dataset, graph or key |
| TigerGraph write-back | **20/20** cases live as `InvestigationCase` vertices (`evidence/tg_live_check.json`) |
| Demo | [`docs/demo/walkthrough.mp4`](docs/demo/walkthrough.mp4) (2:45, narrated) + [`docs/demo/dashboard.html`](docs/demo/dashboard.html) (the real dashboard, offline snapshot) |

The replay flags a *random* transaction of each fraud episode, which is harder than a real alert.

### Beyond the 20 cases: autonomous monitoring ([`monitoring/`](monitoring/))

`python main.py monitor` sweeps November–December without reading the case pack. It opens 15 alerts on its own and investigates each with the same agent and policy engine:

- **12 sub-$500 structuring bursts.** These are the same typology as the 5 confirmed closed cases.
- **The SM-G935F anonymous-proxy device ring.** 28 customers and 60 transactions from Nov 14 to Dec 4. It includes the HHG-014 card.
- **1 smaller *candidate* ring.** One Windows 7 / Chrome 65 / 1916×901 profile, marked New and behind a proxy, used by 3 customers in 5 days. The evidence is thin, and a human should confirm it (it is escalated).
- **1 card-testing sequence (R5).** The R5 shape on its own preceded confirmed fraud in only 2 of 11 Jul–Oct detections. The monitor therefore opens an alert only when the model also finds the follow-up purchase unusual.

## How it works

```
alert ──► txn_context ──► cardholder_baseline ──► card_window + model scoring
            │                                            │
            ▼                                            ▼
   hypothesis tools chosen by what the evidence suggests:        episode reconstruction
   structuring_scan → population sweep · device_neighbors →      (48 h chain across the
   ring check · card_testing_scan · recurring_check (R7)          customer's cards)
            │                                            │
            └──────────────► assessment ◄────────────────┘
                 calibrated case probability + structural likelihood ratios
                 + independent-signal count + GraphRAG precedents
                              │
                     policy.decide()  (pure; R1–R10, 3a, 3b, routes)
                              │
            value of information? ── yes ─► evidence request (simulated reply, recorded)
                              │                              │
                              ▼                              ▼
                        final decision ◄──── policy.decide() again (3b)
                              │
                 write case to agent memory / TigerGraph ─► next case can find it
```

| Layer | Module | What it does |
|---|---|---|
| Store | `src/engine/store.py` | 590,742 transactions + 144,432 identity rows. Every query is `as_of`-bounded and mirrors a GSQL query in `tigergraph/queries/engine_queries.gsql`. |
| Features | `src/engine/features.py` | Causal behavioural features. Prior confirmed fraud per card/cardholder only counts cases already **closed**. |
| Model | `src/engine/model.py` | Gradient-boosted transaction model trained on the bank's own closed cases (the only confirmed outcomes). |
| Episode | `src/engine/episode.py` | Rebuilt from rules measured on the 4,665 confirmed cases: 48 h gaps, spans all of the customer's cards, exposure = sum. |
| Patterns | `src/engine/patterns.py` | README-defined detectors (card testing, structuring, R7 recurring) plus a pattern classifier trained on closed-case labels. |
| Rings | `src/engine/rings.py` | Links cards only through hardware-specific or anomalous devices, so generic "Windows/chrome" profiles never create fake rings. |
| Memory | `src/engine/memory.py` | GraphRAG from three sources: graph hops (customer, cardholder, card, device → closed cases), behavioural kNN over all 5,565 closed cases, and the agent's own earlier cases. |
| Calibration | `src/engine/calibration.py` | Logistic calibrator fitted on replayed closed cases, then prior-shifted to the benchmark's stated 50/50 mix. |
| Policy | `src/engine/policy.py` | Pure function implementing R1–R10, 3a (case vs report), 3b and §2 routes. Pinned by `tests/test_policy.py`. |
| Agent | `src/engine/investigator.py` | Hypothesis-driven tool loop. Records a reasoned trace, simulated replies and stop reasons. |
| Validator | `src/engine/validate.py` | Every mechanically checkable README/policy rule. CI runs it on `cases/`. |

## Quick start

```bash
pip install -e ".[dev]"                  # or: pip install -r requirements.txt
python -m pytest -q                      # 87 passed, 2 skipped — no dataset needed (~1 s)
python main.py validate                   # 20 files, 0 violations — no dataset needed (tier A)

bash scripts/fetch_prepared_data.sh      # dataset extract (see Data provenance)
python main.py build                     # data/store/tx.parquet
python main.py train                     # models/ + data/store/scored.parquet + benchmark/backtest.json (~10 min)
python main.py validate                  # upgrades to tier B: every id + exposure re-derived from the raw rows
python main.py investigate               # cases/HHG-*.json + traces/
python main.py backtest                  # benchmark/case_backtest.json
python main.py monitor                   # monitoring/
python main.py serve                     # dashboard on :8000 (cases, traces, backtest, monitoring)
```

Every `python main.py <cmd>` is also `hhgoa <cmd>` once the package is installed (`[project.scripts]` in
`pyproject.toml`).

`models/` ships the trained artefacts (≈3 MB); the closed-case memory index is rebuilt with
`python scripts/build_memory_index.py` after `train`.

### TigerGraph

- Schema: `tigergraph/schema.gsql`; deployed graph `FraudInvestigation` (1,805 `Customer`, 1,868 `Card`, 10,000 `Transaction`, 9,703 `DeviceProfile`, 5,565 `ClosedCase`).
- Engine queries, all `as_of`-bounded: `tigergraph/queries/engine_queries.gsql`. They cover `txn_context`, `card_window`, `cardholder_baseline`, `device_neighbors`, `prior_cases`, `device_case_links`, `population_structuring_sweep`, `ring_candidates` and `agent_case_links`.
- Set `TIGERGRAPH_HOST`/`TIGERGRAPH_TOKEN` and each finished case is upserted as an `InvestigationCase` vertex. `written_to_graph` is `true` **only** when TigerGraph acknowledges the write.
- **All 20 answer files carry `written_to_graph: true` and a real vertex id (`CASE-HHG-001` … `CASE-HHG-020`).** The flag is not set on a send — `upsert_agent_case` returns `true` only after the vertex is read back from the live instance (`src/graph/tigergraph_client.py`). Proof of that run is checked in: [`evidence/tg_live_check.json`](evidence/tg_live_check.json) (`connected: true`, live host, vertex counts, `written_to_graph: 20`), and the validator rejects a `true` flag without a `graph_case_id` or vice-versa.
- `evidence/legacy_run/` keeps the earlier live Savanna session: 10/10 GSQL smoke queries executed and 5/5 official `tigergraph-mcp` tool checks.
- With no instance configured the same records fall back to the agent-memory graph file `memory/agent_cases.json`, which later investigations read back as precedents; the flag then honestly reads `false`.
- To go live, put the `TIGERGRAPH_*` settings in `.env` (git-ignored), then run `python scripts/tg_live_check.py --install --write`. It connects (API token, GSQL secret or user/password), installs the engine queries and re-runs the 20 cases with graph write-back. Results go to `evidence/tg_live_check.json`.

### LLM use

The engine uses **0 tokens**: every sentence is rendered from structured findings, so every claim is traceable. The legacy LLM orchestrator is still available (`python main.py investigate --legacy`) for the reasoning-narrative demo.

## Data provenance and rules compliance

- The 708 MB Google-Drive original was not downloadable here. `scripts/fetch_prepared_data.sh` pulls a column extract of the **official HHGOA_IEEE files** (same transformed IDs, times and amounts) from a public repository (`kashish-005/hhgoa-fraud-investigation`), whose build scripts read the organisers' `transactions.csv`/`identity.csv`.
- **No Kaggle / public IEEE-CIS file is used.** Outcomes come only from `closed_cases_history.csv`.
- Every ID in `cases/` is checked against the dataset by the validator. Action names and routes are the policy's exact identifiers.
- The seeded rows share a timestamp artefact (seconds = 0). The agent does **not** use it. The structuring and ring detectors are defined by behaviour: amounts, timing, device anomaly and cross-customer spread.

## Submission components

| Required by the brief | Where | State |
|---|---|---|
| Working agent | `src/engine/` (engine) + `main.py` / `hhgoa` CLI | runs on a clean clone; 20/20 cases reproduce |
| GitHub repository | this repo | 311 tracked files, `pip install -e ".[dev]"` |
| 20 answer files (case + SAR + next best action, case written to the graph) | `cases/HHG-001.json … HHG-020.json` | 58-check validator: 0 violations; `written_to_graph: true` 20/20 |
| TigerGraph usage (GSQL, algorithms, MCP, GraphRAG) | `tigergraph/`, `src/mcp/`, `src/engine/memory.py` | live evidence in `evidence/`; vector gap disclosed in `docs/PRD.md` §5 |
| User interface | `src/ui/app.py` → `python main.py serve` | 7 endpoints, dashboard on `:8000` |
| Demo video | `docs/demo/walkthrough.mp4` (2:45, narrated) + `docs/demo/dashboard.html` | rendered from the checked-in artefacts |
| Blog post | `docs/BLOG_POST.md` | 83 lines, every number traceable to `benchmark/` |
| Social post tagging @TigerGraphDB | `docs/SOCIAL_POST.md` | 4-post thread |
| Results / metrics | `docs/RESULTS.md`, `benchmark/` | regenerated by `train` / `backtest` |
| Design record (requirements, phases, contributor rules) | `docs/PRD.md`, `docs/ROADMAP.md`, `docs/BRIEF.md`, `AGENTS.md` | traceability table for R1–R10 and the brief's 10 agent requirements |

## Repository map

| Path | What is in it |
|---|---|
| `src/engine/` | the evidence-first engine: `store` · `features` · `model` · `episode` · `patterns` · `rings` · `memory` · `calibration` · `policy` · `investigator` · `narrative` · `validate` |
| `src/agent`, `src/evidence`, `src/policy`, `src/cases` | legacy v1 orchestrator (rules + LLM), kept for `investigate --legacy` |
| `src/graph/` | TigerGraph client, in-memory graph, data loader |
| `src/mcp/` | `engine_server.py` (7 MCP tools over the engine) · `server.py` (legacy) |
| `src/ui/app.py` | FastAPI analyst dashboard |
| `tigergraph/` | `schema.gsql` (8 vertices, 18 edges) · `queries/engine_queries.gsql` (9 `as_of`-bounded queries) |
| `scripts/` | build · train · backtest · monitor · validate · live TigerGraph check |
| `cases/` · `traces/` | the graded artefacts: 20 answer files + step-by-step reasoning traces |
| `benchmark/` · `monitoring/` · `evidence/` · `models/` | metrics, autonomous alerts, live-graph proof, trained artefacts (~3 MB) |
| `docs/` | `BRIEF.md` (challenge text) · `PRD.md` · `ROADMAP.md` · `ARCHITECTURE.md` · `RESULTS.md` · `BLOG_POST.md` · `SOCIAL_POST.md` · `DEMO_SCRIPT.md` · `demo/` |
| `AGENTS.md` | boundaries and invariants for anyone editing this repo |

