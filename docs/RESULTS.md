# Final Run Results — 20/20 Benchmark Cases
**Run date:** 2026-09-25 (IST) · **Judge model:** `nvidia/nemotron-3-super-120b-a12b` (free tier, via OpenRouter)
**Pipeline:** HYBRID (rules + LLM) · TigerGraph Savanna live queries + GDS-style PageRank · GraphRAG with vector-retrieved precedents (5,565 embedded closed cases) · official tigergraph-mcp verified separately (5/5 live checks, 69 tools)
## Verdict tally
| Verdict | Count | Share |
|---|---|---|
| fraud | 17 | 85% |
| legitimate | 1 | 5% |
| uncertain | 2 | 10% (target ≤ 30%) |
SARs filed: 14 · Total LLM tokens: 211,956 · All 20 cases received real LLM assessments (no rule-fallback cases).
## Per-case results
| Case | Verdict | Prob | Pattern | Exposure | SAR | Tokens |
|---|---|---|---|---|---|---|
| HHG-001 | uncertain | 0.45 | out_of_region_use | — | no | 8,783 |
| HHG-002 | fraud | 0.85 | card_not_present_fraud | $410.94 | no | 4,045 |
| HHG-003 | fraud | 0.50 | card_testing | $831.25 | no | 7,809 |
| HHG-004 | fraud | 1.00 | card_not_present_new_device | $891.85 | yes | 11,378 |
| HHG-005 | fraud | 1.00 | account_takeover | $2,476.77 | yes | 6,188 |
| HHG-006 | fraud | 0.95 | card_not_present_new_device | $1,197.68 | yes | 13,075 |
| HHG-007 | uncertain | 0.50 | undocumented | — | no | 8,367 |
| HHG-008 | fraud | 1.00 | card_not_present_new_device | $72.44 | yes | 12,925 |
| HHG-009 | fraud | 0.80 | card_not_present_fraud | $722.90 | yes | 21,978 |
| HHG-010 | fraud | 0.75 | card_not_present_fraud | $1,221.06 | yes | 10,970 |
| HHG-011 | fraud | 1.00 | card_not_present_new_device | $314.61 | yes | 11,016 |
| HHG-012 | legitimate | 0.12 | card_testing | — | no | 3,934 |
| HHG-013 | fraud | 1.00 | card_not_present_new_device | $911.48 | yes | 11,592 |
| HHG-014 | fraud | 0.60 | card_not_present_fraud | $578.94 | yes | 12,291 |
| HHG-015 | fraud | 0.90 | card_not_present_new_device | $851.16 | yes | 7,833 |
| HHG-016 | fraud | 0.95 | card_testing | $316.67 | yes | 10,118 |
| HHG-017 | fraud | 0.65 | card_testing | $2,930.71 | yes | 11,616 |
| HHG-018 | fraud | 0.55 | account_takeover | $1,700.51 | no | 8,114 |
| HHG-019 | fraud | 0.95 | card_not_present_new_device | $311.86 | yes | 16,162 |
| HHG-020 | fraud | 0.95 | account_takeover | $343.05 | yes | 13,762 |
## Calibration behaviour (T2/T3 evidence)
The bounded-move reassessment patch demonstrably prevents rule-score ratcheting.
Selected log evidence from this run (anchor = LLM Step-6 assessment; rule-recalc = raw rule score):
| Case | Anchor | Rule-recalc | Final | Note |
|---|---|---|---|---|
| HHG-003 | 0.50 | **1.00** | 0.50 | ratchet blocked; would have been 1.00 pre-fix |
| HHG-007 | 0.50 | **1.00** | 0.50 | ratchet blocked |
| HHG-017 | 0.65 | **1.00** | 0.65 | ratchet blocked |
| HHG-018 | 0.55 | **1.00** | 0.55 | ratchet blocked |
Probability distribution is now honest and spread (0.12–1.00, mean ≈ 0.77) instead of
saturated at 1.0. Low-confidence `fraud` verdicts (0.50–0.65) get verification-first
action sets per policy R1 rather than immediate blocks.
## Honest limitations
1. **Fraud skew remains.** 17/20 fraud vs the benchmark's stated ~50% legitimate base
   rate. After the ratchet fix, the residual skew is attributable to (a) the judge
   model's tendency to resolve borderline cases toward `fraud` (nemotron judged
   HHG-003/017 as fraud at 0.50/0.65 where a prior judge said uncertain), and (b)
   rule anchors (pattern weights 0.60–0.85) flooring the ±0.15 lockdown band. We
   report this rather than tune to the answer key.
2. **Judge variance across runs.** Three full runs with three different free judges
   (gpt-oss-120b via Groq, degraded Gemini tail, nemotron via OpenRouter) produced
   tallies of 17/1/2, 15/1/4 (degraded), and 17/1/2 respectively. The final submitted
   set is single-judge (nemotron) for consistency.
3. **TigerVector roundtrip 2/3.** Via the official MCP server we created the vector
   attribute on ClosedCase, upserted 200 vectors, and fetched them back; the top-k
   similarity comparison did not return parseable IDs and is left as future work.
   In-process vector retrieval (hashed TF-IDF, cosine top-k) powers the submitted
   GraphRAG context.
4. **Partial exposures (0.00) on non-fraud cases** reflect that `exposure_usd` is
   computed over connected fraudulent transactions only.
5. **Cosmetic:** on 4 cases (HHG-005, HHG-006, HHG-016, HHG-020) the explanation
   sub-call hit an upstream null-content response; template fallback explanations
   were used. Verdicts/probabilities were unaffected.
## Provenance
- Dataset: HHGOA IEEE-CIS benchmark (590,742 transactions, 13,553 customers, 14,893 cards,
  5,565 closed cases) loaded in-memory and 10K-subset live in TigerGraph Savanna.
- No public IEEE-CIS/Kaggle label files were used; all verdicts derive from the agent's own
  graph evidence + LLM judgment.
- Judge: nvidia/nemotron-3-super-120b-a12b via OpenRouter (free tier), temperature 0,
  responses cached by (provider, model, prompt) for reproducibility.
- Evidence files: `cases/tigergraph_query_results.json` (10/10 live GSQL queries),
  `cases/mcp_tool_verification.json` (official tigergraph-mcp 5/5 checks),
  `cases/run_summary.json` (this run's machine-readable summary).