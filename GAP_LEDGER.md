# GAP LEDGER — maintained after every task

| # | Gap | Task | Status | Evidence | Points | Time spent |
|---|-----|------|--------|----------|--------|------------|
| 1 | Synthetic data, never ran on real dataset | T1 | BLOCKED | See below | +1.2 | 15m |
| 2 | Zero calibration; guessed probability weights | T2 | OPEN | Blocked by T1 | +0.9 | — |
| 3 | 75% of cases end `uncertain` | T3 | OPEN | Blocked by T2 | +0.5 | — |
| 4 | TigerGraph never connected; no GSQL executed | T4 | BLOCKED | Needs TG credentials from human | +1.0 | — |
| 5 | GraphRAG has no vector retrieval | T5 | OPEN | Blocked by T4 | +0.3 | — |
| 6 | MCP server is a stub, not tigergraph-mcp | T6 | PARTIAL | Real tool list documented from official repo | +0.3 | 10m |
| 7 | In-memory engine untested at 590K rows | T7 | OPEN | Blocked by T1 | +0.2 | — |
| 8 | No video, blog unverified, artefacts incomplete | T8 | OPEN | | +0.6 | — |

---

## TASK 1 — Real dataset

**Status: BLOCKED**

**What I tried (all failed):**

```bash
# Attempt 1: kagglehub
$ pip install kagglehub && python3 -c "import kagglehub; kagglehub.dataset_download('kristianbruzkas/ieee-fraud-detection')"
ERROR: SSLError: HTTPSConnectionPool(host='api.kaggle.com', port=443): Max retries exceeded

# Attempt 2: curl to Kaggle
$ curl -sI "https://api.kaggle.com"
(empty response — SSL reset)

# Attempt 3: curl to Google Drive
$ curl -sI "https://drive.google.com"
(empty response — SSL reset)

# Attempt 4: urllib with SSL verification disabled
$ python3 -c "urllib.request.urlopen('https://kaggle.com', context=ssl._create_unverified_context())"
FAIL: TLS/SSL connection has been closed (EOF)

# Attempt 5: gdown
$ gdown --folder "https://drive.google.com/drive/folders/1YDJUW1fiE7Jx8R9KqknC4IcsED9zll2A"
Error: HTTPSConnectionPool(host='drive.google.com', port=443): SSLError
```

**Root cause:** This sandbox blocks outbound HTTPS to all hosts except `api.github.com`. Kaggle, Google Drive, and all CDNs are unreachable. This is a sandbox networking restriction, not a code issue.

**BLOCKED: I need the human to download files locally.**

---

## TASK 1 — HANDOFF REQUEST TO HUMAN

Download these files and place them in your local clone of the repo:

### Files from Google Drive (~2.7 MB total)
Source: https://drive.google.com/drive/folders/1YDJUW1fiE7Jx8R9KqknC4IcsED9zll2A

| File | Size | Target path |
|---|---|---|
| `case_pack.csv` | ~3 KB | `data/HHGOA_IEEE/case_pack.csv` |
| `closed_cases_history.csv` | ~2.6 MB | `data/HHGOA_IEEE/closed_cases_history.csv` |
| `README.md` | ~38 KB | `data/HHGOA_IEEE/README.md` (overwrite existing) |

### Files from Kaggle (~1.35 GB total)
Source: https://www.kaggle.com/competitions/ieee-fraud-detection/data
(Requires free Kaggle account + joining the competition)

| File | Size | Target path |
|---|---|---|
| `train_transaction.csv` | ~690 MB | `data/kaggle/train_transaction.csv` |
| `train_identity.csv` | ~25 MB | `data/kaggle/train_identity.csv` |

Then run:
```bash
# Verify downloads
wc -l data/HHGOA_IEEE/case_pack.csv        # expect: 21 (header + 20 cases)
wc -l data/HHGOA_IEEE/closed_cases_history.csv  # expect: 5566
wc -l data/kaggle/train_transaction.csv     # expect: 590541
wc -l data/kaggle/train_identity.csv        # expect: 144234

# Commit and push
git add data/ && git commit -m "data: add real dataset files" && git push
```

### TigerGraph credentials (Task 4)
Sign up at https://savanna.tgcloud.io, create a workspace named `FraudInvestigation`, then share:
- Workspace URL (e.g. `https://your-workspace.tgcloud.io`)
- API token (Admin → Tokens → Generate)

Put these in `.env`:
```
TIGERGRAPH_HOST=https://your-workspace.tgcloud.io
TIGERGRAPH_TOKEN=your_token_here
```

---

## TASK 6 — MCP (official tigergraph-mcp)

**Status: PARTIAL — tool list verified from real repo**

**Real tool list from `https://github.com/tigergraph/tigergraph-mcp` (verified via fetch_page):**

### Global Schema Operations
- `tigergraph__get_global_schema`

### Graph Operations
- `tigergraph__list_graphs`, `tigergraph__create_graph`, `tigergraph__drop_graph`, `tigergraph__clear_graph_data`

### Schema Operations
- `tigergraph__get_graph_schema`, `tigergraph__show_graph_details`

### Node Operations
- `tigergraph__add_node`, `tigergraph__add_nodes`, `tigergraph__get_node`, `tigergraph__get_nodes`
- `tigergraph__delete_node`, `tigergraph__delete_nodes`, `tigergraph__has_node`, `tigergraph__get_node_edges`

### Edge Operations
- `tigergraph__add_edge`, `tigergraph__add_edges`, `tigergraph__get_edge`, `tigergraph__get_edges`
- `tigergraph__delete_edge`, `tigergraph__delete_edges`, `tigergraph__has_edge`

### Query Operations
- `tigergraph__run_query`, `tigergraph__run_installed_query`, `tigergraph__install_query`, `tigergraph__drop_query`
- `tigergraph__show_query`, `tigergraph__get_query_metadata`, `tigergraph__is_query_installed`

### Loading Job Operations
- `tigergraph__create_loading_job`, `tigergraph__run_loading_job_with_file`, `tigergraph__run_loading_job_with_data`

### Statistics Operations
- `tigergraph__get_vertex_count`, `tigergraph__get_edge_count`, `tigergraph__get_node_degree`

### GSQL Operations
- `tigergraph__gsql` — Execute raw GSQL

### Vector Operations (TigerGraph 4.2+)
- `tigergraph__add_vector_attribute`, `tigergraph__upsert_vectors`, `tigergraph__search_top_k_similarity`

### Discovery & Navigation
- `tigergraph__discover_tools`, `tigergraph__get_workflow`, `tigergraph__get_tool_info`

**Installation:** `pip install tigergraph-mcp`

**Configuration via .env:**
```
TG_HOST=http://localhost
TG_GRAPHNAME=FraudInvestigation
TG_USERNAME=tigergraph
TG_PASSWORD=tigergraph
```

**What needs to happen:** Once the human provides TigerGraph credentials, wire the agent to use `tigergraph-mcp` instead of the custom MCP server.
