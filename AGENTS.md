# AGENTS.md — working rules for humans and coding agents

Read this before changing anything. The repo is graded on artefacts that must stay mutually
consistent: `cases/`, `traces/`, `benchmark/`, `evidence/`, `monitoring/` and the numbers quoted in
`README.md` and `docs/`.

---

## 1. Commands

```bash
pip install -e ".[dev]"          # base install; no dataset, no TigerGraph needed
python -m pytest -q              # 87 passed, 2 skipped (skips want the store / the tigergraph extra)
python -m pytest -q              # with `.[all]` installed: 89 passed, 1 skipped
python main.py validate          # tier A: 20 files, 0 violations (no dataset)

bash scripts/fetch_prepared_data.sh && python main.py build   # data/store/tx.parquet
python main.py train             # models/ + data/store/scored.parquet + benchmark/backtest.json
python main.py validate          # tier B: ids + exposure re-derived from raw rows
python main.py investigate       # rewrites cases/ and traces/, and benchmark/run_summary.json
python main.py backtest          # benchmark/case_backtest.json
python main.py monitor           # monitoring/
python main.py serve --port 8000 # dashboard
```

`hhgoa <cmd>` is the same thing once the package is installed.

## 2. Layer boundaries (violating these is a design bug, not a style nit)

| Module | May | Must not |
|---|---|---|
| `src/engine/store.py` | read parquet, answer `as_of`-bounded queries | decide anything, mention probability |
| `src/engine/model.py`, `patterns.py`, `calibration.py` | produce numbers/probabilities | emit an action string |
| `src/engine/policy.py` | **the only writer of `next_best_actions` and `sar.file`** | read files, call a model, read the clock |
| `src/engine/investigator.py` | choose tools, build `PolicyInput`, call `decide()` | compute a verdict by threshold at the call site |
| `src/engine/narrative.py` | render sentences from structured findings | introduce an entity id that a store query did not return |
| `src/engine/validate.py` | check artefacts | be relaxed to make a run pass |

`policy.decide()` stays pure: same input → same actions. No new parameters that read global state.

## 3. Invariants that must never be edited to make a check pass

1. **Never fabricate a flag.** `written_to_graph` is set from a read-back of the vertex
   (`src/graph/tigergraph_client.upsert_agent_case`). If TigerGraph is unreachable, the honest value is
   `false` and the case goes to `memory/agent_cases.json`. Fix the connection, not the field.
2. **`graph_case_id` and `written_to_graph` agree** — one without the other is a validator error.
3. **Numbers in prose come from artefacts.** If you change a run, re-run
   `python scripts/summarize_run.py` and update `README.md`, `docs/RESULTS.md`, `docs/BLOG_POST.md`,
   `docs/SOCIAL_POST.md` and `docs/PRD.md` in the same commit. Grep for the old value:
   `grep -rn "0\.82\|181\|20 files" README.md docs/`.
4. **No future information.** Every store query takes `as_of`; a closed case may only be used as prior
   art when `closed_at <= as_of` (enforced by `merge_asof` in `src/engine/features.py:35-40`, so a
   transaction can never see the case that later labelled it). Splits are fixed: models train
   Jul–Aug, calibrator fits on Sep, October is the held-out replay month.
5. **No Kaggle/public IEEE-CIS files.** Outcomes come only from `closed_cases_history.csv`.
6. **Never key on the seeding artefact** (`seconds == 0`) or on undocumented columns (`V`, `C`, `D`,
   `M`, numeric `id`) as if they were labelled fraud indicators.
7. **Action names/routes come from Fraud Policy v1.0** — exact identifiers, and `route()` is the only
   route authority.
8. **Do not "fix" a policy violation by editing `validate.py`.** Fix the engine or the case.

## 4. Working loop for a change

1. `python main.py investigate HHG-<id>` (all cases still run first, so memory order is identical).
2. `python main.py validate` — must print `0 violations`, exit 0.
3. `python -m pytest -q` — 87 pass, 2 skip on `.[dev]` (89/1 with `.[all]`); never delete a test to make it pass.
4. If probabilities, actions or SAR counts moved, every quoted metric in `docs/` moves with them.
5. Commit with the phase prefix used in history: `P3 model: ...`, `P8 monitor: ...`.

## 5. Adding a tool

1. Implement it as a store query in `src/engine/store.py`, mirror it in
   `tigergraph/queries/engine_queries.gsql` (must be `as_of`-bounded), and expose it in
   `src/mcp/engine_server.py`.
2. Call it from a hypothesis branch in `investigator.py`, record `why` in the trace.
3. Return structured findings; if it can change a decision, add the field to `PolicyInput` and a rule
   branch to `policy.py`, then a test in `tests/test_policy.py`.
4. Never let a tool write `summary`, `next_best_actions` or `sar`.

## 6. Data notes

* `data/HHGOA_IEEE/case_pack.csv` (20 rows) and `closed_cases_history.csv` (5,565) are checked in.
* `transactions.csv` / `identity.csv` (708 MB) are not. `scripts/fetch_prepared_data.sh` pulls a column
  extract of the same official rows; `data/HHGOA_IEEE/README.md` documents every column.
* Store: 590,742 transactions, 144,432 identity rows, 8 vertex / 18 edge types (`tigergraph/schema.gsql`).
* `models/` ships trained artefacts (~3 MB) so `investigate` is reproducible without retraining.

## 7. Where the honest weakness is

`docs/PRD.md` §5 lists the real gaps (TigerVector not deployed, tier B needs the dataset, simulated
replies). Do not paper over them in README or blog copy — they are part of the submission's credibility.
