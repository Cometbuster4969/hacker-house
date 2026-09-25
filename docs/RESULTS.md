# Results

Engine run: `python main.py investigate` (deterministic, 0 LLM tokens, 164 tool calls, ~0.3 s per case).
Validator: `python main.py validate` → **20 files, 0 violations**. It checks IDs exist, dates are consistent, exact action names and routes, R1–R10, 3a, the SAR/FILE_REPORT agreement and the legitimate-verdict invariants.

## 20 benchmark cases

| Case | Verdict | p | Status | Pattern | Affected | Exposure $ | Evidence asked | Final actions | SAR |
|---|---|---|---|---|---|---|---|---|---|
| HHG-001 | legitimate | 0.08 | closed_legitimate | none | 0 | 0.00 | customer_validation | CREATE_CASE, CLOSE_NO_FRAUD | no |
| HHG-002 | legitimate | 0.08 | closed_legitimate | none | 0 | 0.00 | customer_validation | CREATE_CASE, CLOSE_NO_FRAUD | no |
| HHG-003 | uncertain | 0.71 | open | out_of_region_use | 2 | 165.93 | customer_validation | DECLINE_TRANSACTION, MONITOR_CARD, CREATE_CASE | no |
| HHG-004 | uncertain | 0.41 | escalated | card_not_present_new_device | 1 | 128.33 | customer_validation | DECLINE_TRANSACTION, MONITOR_CARD, CREATE_CASE, ESCALATE_TO_ANALYST | no |
| HHG-005 | legitimate | 0.12 | closed_legitimate | none | 0 | 0.00 | customer_validation | CREATE_CASE, CLOSE_NO_FRAUD | no |
| HHG-006 | fraud | 0.98 | escalated | undocumented | 4 | 1,906.07 | — | BLOCK_CARD, CREATE_CASE, FILE_REPORT, ESCALATE_TO_ANALYST | yes |
| HHG-007 | fraud | 0.98 | closed_fraud | out_of_region_use | 4 | 475.82 | — | BLOCK_CARD, CREATE_CASE | no |
| HHG-008 | fraud | 0.95 | closed_fraud | card_not_present_fraud | 2 | 111.28 | — | BLOCK_CARD, CREATE_CASE | no |
| HHG-009 | fraud | 0.89 | closed_fraud | card_not_present_fraud | 1 | 30.02 | — | BLOCK_CARD, CREATE_CASE | no |
| HHG-010 | legitimate | 0.06 | closed_legitimate | none | 0 | 0.00 | customer_validation | CREATE_CASE, CLOSE_NO_FRAUD | no |
| HHG-011 | uncertain | 0.49 | escalated | card_not_present_new_device | 1 | 131.30 | customer_validation | DECLINE_TRANSACTION, MONITOR_CARD, CREATE_CASE, ESCALATE_TO_ANALYST | no |
| HHG-012 | legitimate | 0.11 | closed_legitimate | none | 0 | 0.00 | customer_validation | CREATE_CASE, CLOSE_NO_FRAUD | no |
| HHG-013 | legitimate | 0.11 | closed_legitimate | none | 0 | 0.00 | customer_validation | CREATE_CASE, CLOSE_NO_FRAUD | no |
| HHG-014 | fraud | 0.88 | escalated | undocumented | 2 | 187.33 | — | BLOCK_CARD, CREATE_CASE, FILE_REPORT, MONITOR_CONNECTED_CARDS, ESCALATE_TO_ANALYST | yes |
| HHG-015 | legitimate | 0.06 | closed_legitimate | none | 0 | 0.00 | customer_validation | CREATE_CASE, CLOSE_NO_FRAUD | no |
| HHG-016 | fraud | 0.91 | closed_fraud | card_not_present_new_device | 1 | 59.67 | — | BLOCK_CARD, CREATE_CASE | no |
| HHG-017 | legitimate | 0.08 | closed_legitimate | none | 0 | 0.00 | customer_validation | CREATE_CASE, CLOSE_NO_FRAUD | no |
| HHG-018 | fraud | 0.98 | closed_fraud | account_takeover | 15 | 2,752.48 | — | BLOCK_CARD, CREATE_CASE, FILE_REPORT | yes |
| HHG-019 | fraud | 0.85 | closed_fraud | card_not_present_new_device | 1 | 99.92 | customer_validation | BLOCK_CARD, CREATE_CASE | no |
| HHG-020 | legitimate | 0.12 | closed_legitimate | none | 0 | 0.00 | customer_validation | CREATE_CASE, CLOSE_NO_FRAUD | no |

Verdict mix: 8 fraud, 9 legitimate, 3 uncertain. The README says half of the cases are legitimate.

The three uncertain cases:

- **HHG-003:** no reply to the verification request. R4 applies: decline, monitor, open a case.
- **HHG-004 and HHG-011:** customer reports where the evidence looks entirely ordinary. The model scores the flagged transactions at 0.01–0.03, and there is no device, region or velocity anomaly. The customer is not assumed to withdraw the dispute. The conflict goes to a human (R8).

## How the agent reached the hard ones

| Case | What decided it | Tools that mattered |
|---|---|---|
| HHG-006 | 4 online product-C purchases, $456.96–488.04, in 30 min, all different amounts. The same shape was on 3 other customers' cards in the prior 30 days and matches 5 confirmed closed cases → undocumented structuring, R9. | structuring_scan → population sweep → prior_cases |
| HHG-014 | Device SM-G935F, marked New and behind an anonymous proxy, on 20 cards of 20 customers in 30 days → R6 shared origin plus R9 undocumented. 19 cards put under monitoring. | device_neighbors → ring check → memory device links |
| HHG-018 | Customer report (denial, R2). The same cardholder already had confirmed fraud in closed cases. Episode of 15 transactions (1 online, 14 in person) → ATO, $2,752 > $2,500 → BLOCK_CARD routed to L2, and the SAR is filed on amount. | cardholder_baseline → episode → pattern model |
| HHG-003 | Moderate evidence: in-person use in a region the card had not used. The customer did not reply. | card_window → verification request |
| HHG-017 | Risk score 0.57, but the model scores the transaction 0.01 for this cardholder. The region is familiar, the device is established, and nothing else in the 10-day window looks fraudulent. The behavioural neighbours lean fraud (4 of 5), but the closed-case pool is 84% fraud, so the agent weights them lightly. The simulated follow-up confirmed the purchase (R3). | model scoring → card_window → GraphRAG → verification request |

## Backtest: the whole agent on October closed cases (`benchmark/case_backtest.json`)

Models were trained on Jul–Aug and the calibrator was fitted on September. 400 confirmed and 144 cleared October cases were replayed, each presented as a plain risk-score alert on a random transaction of the episode.

| Metric | Value |
|---|---|
| Case probability ROC-AUC | 0.844 |
| Brier (50/50 weighting) | 0.165 |
| Verdict accuracy on decided cases (50/50) | 0.819 |
| … counting uncertain as wrong | 0.735 |
| Uncertain rate | 10.5% |
| Blocks on cleared cases | 8.3% |
| Blocks on confirmed cases | 64.5% |
| Episode mean Jaccard (fraud found) | 0.822 |
| Exposure within 10% | 63.7% |
| Pattern accuracy | 0.769 |
| SAR decision agreement | 0.91 |
| Mean tool calls / latency | 8.1 / 0.17 s |

## Component backtests (`benchmark/backtest.json`)

| Component | Result |
|---|---|
| Transaction model, Sep–Oct (193k txns) | ROC-AUC 0.947 (bank risk score 0.861), Brier 0.018 |
| Case replay, 1,347 Oct cases, calibrated | ROC-AUC 0.809. Flagged-transaction score alone: 0.790 |
| Episode reconstruction | Mean Jaccard 0.838 at the chosen threshold |
| Pattern classifier | Accuracy 0.796. CNP 0.98, CNP-new-device 0.87, OOR 0.69, ATO 0.63 |

## Autonomous monitoring (`monitoring/`)

The monitor opened 15 alerts in Nov–Dec, without reading the case pack:

- 12 structuring bursts;
- the SM-G935F device ring (28 customers, 60 transactions);
- 1 small candidate ring, to be confirmed by a human;
- 1 card-testing sequence.

Every alert is investigated by the same agent and policy engine.

## Limitations (stated plainly)

- The submission deadline has passed. These are post-deadline results.
- There were no TigerGraph credentials in this environment, so every file says `written_to_graph: false`. Cases were persisted to `memory/agent_cases.json`, which later investigations do read. The GSQL for every tool is in `tigergraph/queries/engine_queries.gsql`. Earlier live-Savanna evidence is in `evidence/legacy_run/`.
- Customer replies are simulated deterministically from the evidence-only probability, as policy §5 asks. The rule is stated in each file.
- The dataset came from a public extract of the official files (see the README, "Data provenance"), not from the Google-Drive original.
- The benchmark's hidden labels were not available. The backtest measures agreement with the bank's analysts on closed cases, which is the closest honest proxy.
