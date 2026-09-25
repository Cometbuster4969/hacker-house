# Architecture

## Design principles

1. **Evidence before verdict.** A risk score is a reason to look. The agent's probability comes from a model trained on the bank's *own* confirmed outcomes (closed cases), plus structural findings from the graph.
2. **Time discipline.** Every query is bounded by the case's `opened_at`. Precedents count only if they were *closed* before that moment. Cardholder "prior fraud" features count only cases already closed.
3. **The policy is code.** `policy.decide()` is a pure function. It is the only writer of actions, routes and `sar.file`. The narrative can never contradict it.
4. **No fake rings.** A link between cards needs a hardware-specific device profile, or anomalous shared use (New device + proxy on ≥80% of rows), across ≥3 customers in 30 days. Links on generic profiles, e-mail domains or missing values are refused.
5. **Honest flags.** `tokens` counts real LLM tokens (0 for the engine). `written_to_graph` is true only when TigerGraph acknowledged the write. Simulated customer replies are labelled as simulated.

## Data flow

```
official CSVs ─► store.build_store ─► data/store/tx.parquet (590,742 rows)
                         │
          features.build_features (causal)
                         │
    model.train (HistGradientBoosting) ─► p_txn for every transaction
                         │
   ┌─────────── investigator.investigate(case) ───────────┐
   │ txn_context → cardholder_baseline → card_window       │
   │ → hypothesis tools → episode → assessment → memory    │
   │ → policy(initial) → evidence request? → policy(final) │
   │ → write case to memory/TigerGraph → render            │
   └───────────────────────────────────────────────────────┘
                         │
          cases/HHG-*.json, traces/HHG-*.json
```

## Learned components and how they were validated

| Component | Training data | Validation |
|---|---|---|
| Transaction model | 397k Jul–Oct transactions. Label = in a confirmed closed case | Train Jul–Aug, test Sep–Oct: ROC-AUC 0.947 (risk score 0.861) |
| Pattern classifier | Episodes of the 4,665 confirmed cases (analyst's pattern label) | Train on cases opened before Sep, test Sep–Oct: accuracy 0.80 |
| Case calibrator | Replayed Sep–Oct cases (confirmed vs cleared) | Fit on Sep, test on Oct: case ROC-AUC 0.81. Then prior-shifted 89% → 50% |
| Episode rule | Measured on closed cases (48 h gap, whole customer) | Mean Jaccard 0.84 on Sep–Oct |

`scripts/backtest.py` then replays October closed cases through the **entire agent**, using backtest-arm models only (`benchmark/case_backtest.json`).

## The cardholder key

A dataset "customer" is derived from issuer fields and pools many real people. The standard IEEE-CIS client key is used as a `Cardholder` identity: card + billing region + account-open day (`day − D1`). It is computed from dataset columns only. "This cardholder already had confirmed fraud" is the strongest single signal in the data, and the key is also the unit of case memory.

## Undocumented typologies (found, not forced)

| Typology | Signature (behavioural) | Closed-case precedent | In the benchmark |
|---|---|---|---|
| Sub-$500 structuring | ≥3 online purchases within 60 min, each 85–100% of $500, all amounts different (organic bursts repeat one price, e.g. $499.95 × 4) | CC-3748, 3841, 3907, 4086, 4124 | HHG-006 (+3 other cards in 30 days) |
| Anonymous-proxy device ring | One device profile, New + anonymous proxy on ≥80% of rows, ≥3 customers in 30 days | CC-2649, 2971, 2985, 3035 | HHG-014 (20 cards / 20 customers) |

The structuring detector finds exactly 22 bursts in the whole dataset, all product C. Ten are in September (five of them are the closed cases) and twelve are in Nov–Dec. It uses no timestamp artefact.

## Evidence requests and simulated replies

Policy §5 asks us to simulate replies. The agent asks only when the stopping rule (§6) is not met. The reply is derived deterministically from the *evidence-only* probability, and the rule is written into every `evidence_requests[].assumed_response` and trace:

| Evidence-only probability | Risk-score / analyst alert | Customer report |
|---|---|---|
| ≥ 0.60 | customer denies → R2 | customer maintains the dispute → R2 |
| < 0.40 (< 0.20 for reports) | customer confirms → R3 | customer recognises it → R3 |
| otherwise | no reply → R4 (+R8 if conflicting) | no reply → R4 (+R8) |

Likelihood ratios applied to the answer: denial ×2.5, confirmation ×¼. The README's own example moves 0.72 → 0.86, which is ×2.4.

## TigerGraph mapping

`store.InvestigationStore` methods and `tigergraph/queries/engine_queries.gsql` are one-to-one:

| Store method | GSQL query |
|---|---|
| `txn` | `txn_context` |
| `customer_window` | `card_window` |
| `customer_history` | `cardholder_baseline` |
| `device_neighbors` | `device_neighbors` (+ `device_case_links`) |
| `prior_cases` | `prior_cases` |
| `rings.structuring_ring` | `population_structuring_sweep` |
| monitor ring sweep | `ring_candidates` (degree centrality on the device projection) |
| `memory.agent_links` | `agent_case_links` |

With `TIGERGRAPH_HOST`/`TIGERGRAPH_TOKEN` set, `runner.graph_writer_from_env()` upserts each case as an `InvestigationCase` with `CASE_INVOLVES_TXN`/`CASE_ON_CARD`/`CASE_INVOLVES_DEVICE` edges.

## MCP

`python -m src.mcp.engine_server` is a stdio MCP server built on the official `mcp` SDK. It exposes `txn_context`, `card_window`, `device_neighbors`, `prior_cases`, `structuring_sweep`, `investigate` and `policy_decide`. It can run next to the official `tigergraph-mcp` (see `evidence/legacy_run/mcp_tool_verification.json`).
