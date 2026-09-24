# 🕵️ Agentic Fraud Investigation Agent — TigerGraph + AI
**HHGOA 2026 — TigerGraph Partner Challenge**
An AI-powered fraud investigation agent that uses TigerGraph for graph-based evidence gathering, pattern detection, and case memory. The agent investigates fraud alerts, creates and progresses cases, recommends next-best-actions, and explains its reasoning — all under uncertainty.
## Results (final run — 2026-09-25)
All 20 benchmark cases investigated end-to-end: live TigerGraph evidence (11 installed GSQL queries
including a hand-written GDS-style PageRank), GraphRAG context with vector-retrieved precedents over
all 5,565 closed cases, and a single judge model (nvidia/nemotron-3-super-120b-a12b via OpenRouter,
temperature 0, responses cached for reproducibility).
**Verdicts: 17 fraud / 1 legitimate / 2 uncertain · 14 SARs · 211,956 tokens · 0 rule-fallback cases.**
Bounded-move reassessment kept LLM probability anchors against rule-score ratcheting
(HHG-003/007/017/018 held at 0.50–0.65 while the raw rule score saturated at 1.00).
Full per-case table, calibration evidence, provenance and honest limitations:
[`docs/RESULTS.md`](docs/RESULTS.md).
**Required gates, with live evidence:**
- TigerGraph live: 10/10 GSQL query smoke-run — `cases/tigergraph_query_results.json`
- Official `tigergraph-mcp`: 5/5 tool checks, 69 tools exposed — `cases/mcp_tool_verification.json`
- Machine-readable run summary — `cases/run_summary.json`
## Architecture
```
┌──────────────────────────────────────────────────────────────────┐
│                    Fraud Investigation Agent                      │
│                                                                   │
│  ┌─────────┐    ┌──────────────┐    ┌───────────────────────┐   │
│  │  Trigger │───▶│  Orchestrator │───▶│  Policy Engine (R1-10)│   │
│  │  (Alert) │    │ (10-step flow)│    │  + SAR Generator      │   │
│  └─────────┘    └──────┬───────┘    └───────────────────────┘   │
│                        │                                          │
│         ┌──────────────┼──────────────┐                          │
│         ▼              ▼              ▼                          │
│  ┌─────────────┐ ┌──────────┐ ┌──────────────┐                 │
│  │   Evidence   │ │ Pattern  │ │  GraphRAG    │                 │
│  │   Gatherer   │ │ Detector │ │ + Vector     │                 │
│  │              │ │          │ │  Retrieval   │                 │
│  └──────┬──────┘ └────┬─────┘ └──────┬───────┘                 │
│         │              │              │                          │
│         └──────────────┼──────────────┘                          │
│                        ▼                                          │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │              TigerGraph (In-Memory / Savanna)                ││
│  │  Customer ──OWNS──▶ Card ──MADE──▶ Transaction               ││
│  │  Transaction ──FROM_DEVICE──▶ DeviceProfile                  ││
│  │  Transaction ──BILLED_IN──▶ BillingRegion                    ││
│  │  ClosedCase ──INVOLVES──▶ Transaction (+ VECTOR attribute)   ││
│  │  InvestigationCase ──links──▶ all entities                   ││
│  └─────────────────────────────────────────────────────────────┘│
│                        │                                          │
│                        ▼                                          │
│  ┌─────────────────────────────────────────┐                    │
│  │  MCP: in-process tools (15) + official   │                    │
│  │  tigergraph-mcp verified live (69 tools) │                    │
│  └─────────────────────────────────────────┘                    │
│                        │                                          │
│                        ▼                                          │
│  ┌─────────────────────────────────────────┐                    │
│  │       Web Dashboard (FastAPI + HTML)     │                    │
│  │  Case list, evidence viewer, SAR view,   │                    │
│  │  action recommendations, graph stats     │                    │
│  └─────────────────────────────────────────┘                    │
└──────────────────────────────────────────────────────────────────┘
```
## Key Components
### 1. TigerGraph Schema & Graph Engine
- **8 vertex types**: Customer, Card, Transaction, DeviceProfile, EmailDomain, BillingRegion, ClosedCase, InvestigationCase
- **20 edge types** for relationships between all entities
- In-memory graph engine (590K+ transactions) for local development; live TigerGraph Savanna deployment with 11 installed GSQL queries
- GSQL schema at `tigergraph/schema.gsql`
### 2. Evidence Gatherer (`src/evidence/gatherer.py`)
- Queries the graph for transaction history, device connections, billing regions
- Detects card testing, out-of-region use, new device patterns
- Retrieves similar closed cases from case memory
### 3. Pattern Detector (`src/evidence/pattern_detector.py`)
- Detects 5 known fraud patterns: card testing, CNP fraud, CNP new device, out-of-region, account takeover
- Identifies undocumented patterns (coordinated abuse, fraud rings)
- Calculates calibrated fraud probability from evidence
### 4. Policy Engine (`src/policy/engine.py`)
- Enforces rules R1-R10 from the bank's fraud policy
- Routes actions to auto/L1/L2 approval
- Determines SAR filing requirements
- Manages investigation stopping rules
### 5. Agent Orchestrator (`src/agent/orchestrator.py`)
- 10-step investigation cycle: Trigger → Investigate → Gather → Assess → Evidence needs → Reassess (bounded moves) → Actions → Explain → Memory
- Evidence requests with simulated responses
- **Bounded-move reassessment**: after new evidence, the LLM's Step-6 probability stays the anchor and may move ±0.15 at most — raw rule scores can never ratchet a case to 1.0
- Verdict/probability consistency guard (fraud < 0.50 or legitimate > 0.60 → uncertain)
- Case creation and graph writes for case memory
### 6. TigerGraph MCP (`src/mcp/server.py` + official server)
- In-process tool server: 15 tools in OpenAI function-calling format (transactions, devices, patterns, velocity, closed cases)
- **Official `tigergraph-mcp` verified live against Savanna**: 69 tools over stdio, 5/5 smoke checks passed (list_connections, global schema, get_vertex_count, run_installed_query, gsql) — see `scripts/verify_t6_mcp.py` and `cases/mcp_tool_verification.json`
- TigerVector probe via MCP: vector attribute created on ClosedCase, 200 vectors upserted and fetched back (top-k comparison pending upstream — documented honestly)
### 7. GraphRAG Context (`src/evidence/graphrag.py`)
- Synthesizes graph evidence + policy rules for LLM grounding
- Structured context sections for investigation reasoning
- Section 7 feeds **vector-retrieved precedent cases** with outcome/pattern/exposure and calibration guidance
### 8. Vector Retrieval (`src/evidence/vector_retrieval.py`)
- All 5,565 closed cases embedded into a 128-dim space (deterministic hashed TF-IDF, IDF-weighted, L2-normalised — no external embedding API, stable across runs for judging)
- Cosine top-k retrieval: semantically similar precedents surface even with zero entity overlap
- 5,565-case index builds in ~0.6 s, queries in ~30 ms; covered by tests in `tests/test_vector_retrieval.py`
### 9. Web Dashboard (`src/ui/app.py`)
- Dark-mode analyst interface
- Case list with verdict/probability/pattern
- Detailed case view with tabs: Summary, Evidence, Actions, SAR, Case Memory
- Real-time graph statistics
## Quick Start
### Prerequisites
- Python 3.11+
- Dataset files in `data/HHGOA_IEEE/` (see dataset section below)
- For live TigerGraph: a Savanna/CE instance + `TIGERGRAPH_*` vars in `.env`
- For LLM reasoning: any OpenAI-compatible key (`OPENAI_API_KEY`, optional `OPENAI_BASE_URL`; `OPENAI_RPM` enables a client-side pacer for free-tier limits)
### Install
```bash
pip install -r requirements.txt
pip install tigergraph-mcp   # for the official MCP verification
```
### Run Investigation (all 20 cases)
```bash
python scripts/check_llm.py    # 10-second preflight: provider, base_url, one smoke call
python main.py investigate
python scripts/summarize_run.py  # verdict tally + no-LLM suspect detection
```
### Run Single Case
```bash
python main.py investigate HHG-001
```
### Start Dashboard
```bash
python main.py serve
# Open http://localhost:8000
```
### Show Graph Stats
```bash
python main.py stats
```
### Live TigerGraph (optional)
```bash
python scripts/load_to_tigergraph.py   # upsert demo subset via REST++
python scripts/final_queries.py        # install + smoke-run all GSQL queries
python scripts/verify_t6_mcp.py        # official tigergraph-mcp end-to-end check
```
## Dataset
Download from [HHGOA_IEEE Google Drive](https://drive.google.com/drive/folders/1YDJUW1fiE7Jx8R9KqknC4IcsED9zll2A) and place files in `data/HHGOA_IEEE/`:
- `transactions.csv` (~708 MB)
- `identity.csv` (~25 MB)
- `closed_cases_history.csv` (~2.6 MB)
- `case_pack.csv` (~3 KB)
## Project Structure
```
├── main.py                          # CLI entry point
├── requirements.txt                 # Python dependencies
├── LOCAL_SETUP.sh                   # End-to-end local setup
├── GAP_LEDGER.md                    # Task-by-task gap log with evidence
├── tigergraph/
│   ├── schema.gsql                  # TigerGraph GSQL schema
│   └── install_schema.gsql          # Savanna-compatible installer
├── src/
│   ├── utils/
│   │   ├── config.py                # Configuration from env
│   │   └── models.py                # Pydantic data models
│   ├── graph/
│   │   ├── in_memory_graph.py       # In-memory graph engine
│   │   ├── data_loader.py           # CSV → graph loader
│   │   └── tigergraph_client.py     # Savanna REST++/GSQL client
│   ├── evidence/
│   │   ├── gatherer.py              # Evidence collection from graph
│   │   ├── pattern_detector.py      # Fraud pattern detection
│   │   ├── graphrag.py              # GraphRAG context synthesis
│   │   └── vector_retrieval.py      # Case vector index (cosine top-k)
│   ├── policy/
│   │   └── engine.py                # Policy rules R1-R10
│   ├── agent/
│   │   ├── orchestrator.py          # Investigation orchestrator
│   │   └── llm_reasoner.py          # LLM judge (cache, repair, pacing)
│   ├── mcp/
│   │   └── server.py                # In-process MCP-style tools
│   └── ui/
│       └── app.py                   # FastAPI web dashboard
├── scripts/
│   ├── load_to_tigergraph.py        # REST++ bulk upsert (Savanna)
│   ├── final_queries.py             # GSQL install + live smoke evidence
│   ├── verify_t6_mcp.py             # Official tigergraph-mcp verification
│   ├── check_llm.py                 # LLM preflight (10 s)
│   ├── summarize_run.py             # Post-run tally + integrity flags
│   └── patch_*.py                   # Idempotent updaters for local files
├── cases/                           # Output: 20 answer JSONs + evidence
├── data/
│   └── HHGOA_IEEE/                  # Dataset files
├── tests/                           # Test suite (incl. vector retrieval)
└── docs/
    ├── RESULTS.md                   # Final run: table, calibration, limits
    ├── BLOG_POST.md
    └── SOCIAL_POST.md
```
## Answer Format
Each case produces a JSON file with three parts:
1. **Case**: Internal investigation record with evidence, findings, verdict
2. **SAR**: Suspicious Activity Report (when policy requires)
3. **Next Best Actions**: Initial and final recommendations with approval routes
See `data/HHGOA_IEEE/README.md` for the complete specification.
## TigerGraph Usage
### Schema Design
- **Customer** → OWNS → **Card** → MADE → **Transaction**
- **Transaction** → FROM_DEVICE → **DeviceProfile** (online only)
- **Transaction** → BILLED_IN → **BillingRegion**
- **ClosedCase** → INVOLVES → **Transaction**
- **InvestigationCase** → links to all relevant entities
### Live GSQL queries (installed on Savanna, 10/10 smoke-run OK)
`graph_stats`, `card_activity`, `card_txns`, `customer_cards`, `card_case_history`,
`txn_devices`, `txn_emails`, `device_txns`, `region_txns`, `email_domain_activity`,
plus `fraud_pagerank` — a hand-written GSQL PageRank (GDS-style signature) over
Card–Transaction–Device topology, since this Savanna build does not expose the
GDS featurizer through pyTigerGraph.
### Graph Algorithms Used
- **PageRank (GSQL)**: `fraud_pagerank` over card/device topology
- **Breadth-first traversal**: Card → Transaction → Device → Other Cards
- **Pattern matching**: Card testing sequences, velocity spikes
- **Community detection**: Shared device profiles across cards
- **Vector similarity**: Hashed-TF-IDF cosine top-k over closed-case narratives (in-process), with TigerVector persistence exercised through the official MCP server
### Case Memory
Each completed investigation is written back to the graph as an `InvestigationCase` vertex with edges to all relevant entities. Future investigations retrieve similar cases through graph queries *and* vector similarity.
## Agentic Capabilities
1. **Multi-step investigation**: 10-step cycle with evidence gathering and bounded-move reassessment
2. **Tool use**: 15 in-process tools + 69 tools via the official tigergraph-mcp server
3. **Memory**: Case memory through graph writes, graph retrieval, and vector retrieval
4. **Policy compliance**: R1-R10 rule enforcement with approval routing
5. **Uncertainty handling**: Calibrated probability with stopping rules and a ±0.15 reassessment bound
6. **Evidence requests**: Customer verification, step-up auth simulation
7. **Explainability**: Evidence-based reasoning with citations
## What We Learned
1. **Graph-native investigation is powerful**: Tracing money through shared devices, regions, and cards reveals patterns invisible to row-based analysis
2. **Calibration matters**: An agent that blocks everything scores badly — half the cases are legitimate
3. **Bounded LLM moves beat raw scores**: recomputing rule scores after the LLM amended state saturated probabilities at 1.0 and inverted the verdict distribution; anchoring on the LLM assessment with a ±0.15 band restored an honest probability spread (0.12–1.00)
4. **Case memory is a multiplier**: Prior investigations become evidence for future ones — vector retrieval surfaces precedents entity overlap misses
5. **Policy compliance requires careful engineering**: The gap between "recommend" and "execute" must be enforced
6. **Free-tier reality**: the entire benchmark is reproducible on free LLM tiers (single judge, ~212K tokens) with client-side pacing and caching — see `docs/RESULTS.md` provenance
## What We'd Improve
- Complete the TigerVector top-k roundtrip (attribute creation + upsert + fetch proven; similarity ranking response parsing pending) so retrieval runs fully inside TigerGraph
- Load the full 590K-transaction corpus into Savanna (currently 10K live subset + in-memory full graph)
- Interactive evidence gathering with real customer/analyst responses
- Streaming investigation visualization
- Multi-agent collaboration for complex fraud rings

