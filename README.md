# 🕵️ Agentic Fraud Investigation — TigerGraph + a learned, policy-bound agent

**HHGOA 2026 — TigerGraph Partner Challenge (Agentic Fraud Investigation)**

> The submission deadline (24 Sept 2026 IST) has passed. This branch is a post-deadline merge of two
> earlier solutions (this repo's rules+LLM agent and the "anvesh" policy-engine prototype). It was
> rebuilt around an evidence-first engine (`src/engine/`) and checked against an honest,
> leakage-free backtest.

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
pip install -r requirements.txt
bash scripts/fetch_prepared_data.sh      # dataset extract (see Data provenance)
python main.py build                     # data/store/tx.parquet
python main.py train                     # models/ + benchmark/backtest.json (~10 min)
python main.py investigate               # cases/HHG-*.json + traces/
python main.py validate                  # 20 files, 0 violations
python main.py backtest                  # benchmark/case_backtest.json
python main.py monitor                   # monitoring/
python main.py serve                     # dashboard on :8000 (cases, traces, backtest, monitoring)
pytest -q
```

`models/` ships the trained artefacts (≈3 MB); the closed-case memory index is rebuilt with
`python scripts/build_memory_index.py` after `train`.

### TigerGraph

- Schema: `tigergraph/schema.gsql`.
- Engine queries, all `as_of`-bounded: `tigergraph/queries/engine_queries.gsql`. They cover `txn_context`, `card_window`, `cardholder_baseline`, `device_neighbors`, `prior_cases`, `device_case_links`, `population_structuring_sweep`, `ring_candidates` and `agent_case_links`.
- Set `TIGERGRAPH_HOST`/`TIGERGRAPH_TOKEN` and each finished case is upserted as an `InvestigationCase` vertex. `written_to_graph` is `true` **only** when TigerGraph acknowledges the write.
- No TigerGraph instance was reachable in the environment that produced these answer files, so every file honestly says `written_to_graph: false`. Cases went to the agent-memory graph file `memory/agent_cases.json` instead, which later investigations read.
- Evidence from the earlier live Savanna session (10/10 GSQL smoke run, official `tigergraph-mcp` 5/5 tool checks) is kept in `evidence/legacy_run/`.

### LLM use

The engine uses **0 tokens**: every sentence is rendered from structured findings, so every claim is traceable. The legacy LLM orchestrator is still available (`python main.py investigate --legacy`) for the reasoning-narrative demo.

## Data provenance and rules compliance

- The 708 MB Google-Drive original was not downloadable here. `scripts/fetch_prepared_data.sh` pulls a column extract of the **official HHGOA_IEEE files** (same transformed IDs, times and amounts) from a public repository (`kashish-005/hhgoa-fraud-investigation`), whose build scripts read the organisers' `transactions.csv`/`identity.csv`.
- **No Kaggle / public IEEE-CIS file is used.** Outcomes come only from `closed_cases_history.csv`.
- Every ID in `cases/` is checked against the dataset by the validator. Action names and routes are the policy's exact identifiers.
- The seeded rows share a timestamp artefact (seconds = 0). The agent does **not** use it. The structuring and ring detectors are defined by behaviour: amounts, timing, device anomaly and cross-customer spread.

## Repository map

`src/engine/` new engine · `src/agent`, `src/evidence`, `src/policy` legacy orchestrator ·
`src/mcp/server.py` MCP tools · `src/ui/app.py` dashboard · `tigergraph/` schema + GSQL ·
`scripts/` build/train/backtest/monitor/validate · `benchmark/` metrics · `traces/` agent traces ·
`monitoring/` autonomous alerts · `docs/` architecture, results, blog, demo script.
