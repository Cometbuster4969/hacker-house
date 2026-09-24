# Building an Agentic Fraud Investigation Agent with TigerGraph

## HHGOA 2026 — TigerGraph Partner Challenge

### The Problem

Fraud analysts at financial institutions face an impossible workload. Each alert requires manually gathering transaction history, tracing money movement across accounts, identifying connected devices and cards, reviewing policies, assessing risk, and deciding what action to take — all under time pressure. By the time the investigation is complete, the money is often already gone.

We built an **AI-powered Fraud Investigation Agent** that automates this entire investigation lifecycle, powered by **TigerGraph** for graph-based evidence gathering and case memory.

### What We Built

Our agent takes a fraud alert — triggered by a risk score, customer report, or analyst request — and runs a complete 10-step investigation:

1. **Trigger**: Accept the alert and identify the flagged transaction
2. **Investigate**: Query the knowledge graph for transaction history, device connections, billing regions, and email domains
3. **Gather Evidence**: Collect structured evidence from graph traversals — card velocity, shared devices, connected cards, region patterns
4. **Assess Uncertainty**: Detect fraud patterns (5 known + undocumented) and get a calibrated probability from the GraphRAG-grounded LLM judge
5. **Identify Evidence Needs**: Decide what additional evidence would reduce uncertainty
6. **Gather More Evidence**: Request customer verification or step-up authentication when signals are ambiguous
7. **Reassess (bounded moves)**: Incorporate new evidence while keeping the LLM probability as the anchor (max ±0.15 move — rule scores can never ratchet a case to 1.0)
8. **Take Actions**: Recommend next-best-actions with proper approval routing (auto/L1/L2)
9. **Explain**: Generate evidence-based reasoning with policy rule citations
10. **Update Memory**: Write the completed case back to the graph for future investigations

### Architecture

```
┌─────────────────────────────────────────────┐
│           Fraud Investigation Agent          │
│                                              │
│  Trigger → Orchestrator → Policy Engine      │
│                ↓                              │
│  Evidence Gatherer + Pattern Detector         │
│  + GraphRAG (vector-retrieved precedents)     │
│                ↓                              │
│  TigerGraph (Savanna live / in-memory dev)   │
│  8 vertex types, 20 edge types,              │
│  11 installed GSQL queries                   │
│                ↓                              │
│  Official tigergraph-mcp (69 tools)          │
│  + 15 in-process investigation tools         │
│                ↓                              │
│  LLM judge (GraphRAG-grounded, bounded moves)│
│                ↓                              │
│  Web Dashboard (FastAPI + HTML)              │
└─────────────────────────────────────────────┘
```

### How TigerGraph Is Used

#### Graph Schema
We designed a schema with 8 vertex types modeling the financial entity graph:
- **Customer** → OWNS → **Card** → MADE → **Transaction**
- **Transaction** → FROM_DEVICE → **DeviceProfile** (online transactions)
- **Transaction** → BILLED_IN → **BillingRegion**
- **Transaction** → PURCHASER_EMAIL / RECIPIENT_EMAIL → **EmailDomain**
- **ClosedCase** → INVOLVES → **Transaction** / ON_CARD → **Card**

#### Live Savanna Deployment & GSQL
The schema is deployed on **TigerGraph Savanna** with **11 installed GSQL queries**
(all smoke-run live — evidence in `cases/tigergraph_query_results.json`):
`graph_stats`, `card_activity`, `card_txns`, `customer_cards`, `card_case_history`,
`txn_devices`, `txn_emails`, `device_txns`, `region_txns`, `email_domain_activity`,
plus **`fraud_pagerank`** — a hand-written GDS-style PageRank over the
Card–Transaction–Device topology. For local development, an in-memory engine
holds the full 590K-transaction graph.

#### Graph Algorithms
- **PageRank (GSQL)**: `fraud_pagerank` over card/device topology
- **Breadth-first traversal**: From a flagged transaction → card → all card transactions → devices → other cards on same devices
- **Pattern matching**: Card testing detection (small authorizations → large purchase), out-of-region use, new device detection
- **Community detection**: Finding shared device profiles across multiple cards (fraud ring indicators)
- **Vector similarity**: All 5,565 closed-case narratives embedded (deterministic hashed TF-IDF, 128-dim, cosine top-k) so semantically similar precedents surface even with zero entity overlap

#### TigerGraph MCP
We use the **official [`tigergraph-mcp`](https://github.com/tigergraph/tigergraph-mcp) server,
verified live against Savanna**: 69 tools over stdio, 5/5 smoke checks passed
(connections, global schema, vertex counts, installed queries, raw GSQL) — evidence in
`cases/mcp_tool_verification.json`. Through it we also exercised TigerVector: created a
vector attribute on `ClosedCase` and upserted 200 case embeddings.

Alongside it, an in-process tool server exposes 15 investigation-specific tools in
OpenAI function-calling format:
- `get_card_transactions`, `get_customer_cards`, `get_device_profile`
- `detect_card_testing`, `detect_out_of_region`, `get_card_velocity`
- `find_shared_devices`, `find_similar_closed_cases`
- `write_investigation_case` (for case memory)

#### GraphRAG + LLM Reasoning
Graph evidence, policy rules, and **vector-retrieved precedent cases** are synthesized
into structured context and passed to the LLM judge
(nvidia/nemotron-3-super-120b-a12b via OpenRouter, temperature 0, responses cached for
reproducibility) — the model reasons over grounded evidence, it never sees raw tables.
A **bounded-move reassessment** keeps the LLM's probability as the anchor after new
evidence arrives (max ±0.15 move), preventing rule scores from ratcheting every case to 1.0.

#### Case Memory
Each completed investigation is written back to the graph as an `InvestigationCase` vertex with edges to all relevant entities. When a new investigation starts, the agent retrieves similar past cases through graph traversal *and* vector similarity. This creates a feedback loop where investigations improve over time.

### Agentic Capabilities

1. **Multi-step investigation workflow**: The agent follows a 10-step cycle, not a single-shot classification
2. **Tool use via MCP**: 15 graph operation tools, callable through the Model Context Protocol
3. **Policy compliance engine**: Rules R1-R10 enforced programmatically, with approval routing
4. **Uncertainty-aware reasoning**: Calibrated probability with explicit stopping rules
5. **Evidence requests**: The agent can request customer verification and simulate responses
6. **Case memory**: Graph-based storage and retrieval of investigation history
7. **Explainability**: Every recommendation cites the evidence and policy rule

### The Policy Engine

The bank's fraud policy has 10 rules (R1-R10) that govern when to block, verify, escalate, or close. Our policy engine implements each one:

- **R1**: Verify before blocking on weak signals (probability < 0.70)
- **R2**: Customer denies → block card, create case, file SAR if exposure > $1,000
- **R3**: Customer confirms → close as legitimate
- **R5**: Card testing (3+ small txns → large purchase) → decline and step-up auth
- **R6**: Shared origin across cards → create case, file report, monitor connected
- **R8**: Escalate when uncertain and exposed (>$500)
- **R9**: Undocumented patterns → create case, file report, escalate
- **R10**: Never block all cards without confirmed multi-card compromise

### What We Learned

1. **Graph-native investigation is powerful.** Tracing money through shared devices, regions, and cards reveals patterns invisible to row-based analysis. A single device profile used across three cards is the kind of connection that only a graph makes obvious.

2. **Calibration matters more than accuracy.** An agent that blocks everything would catch all fraud — and destroy customer trust. The dataset README warns that "half the cases are legitimate." Getting the probability right is harder than getting the classification right.

3. **Case memory is a multiplier.** Each investigation that writes back to the graph becomes evidence for the next one. Over time, the system builds institutional knowledge that no individual analyst could carry.

4. **Policy compliance requires careful engineering.** The gap between "recommend" and "execute" matters. An agent that auto-blocks without approval routing is a liability, not an asset.

5. **The undocumented patterns are the real test.** The five known fraud patterns are table stakes. The challenge is noticing activity that fits none of them and describing it in your own words.

### What We'd Improve With More Time

- **Complete the TigerVector top-k roundtrip**: attribute creation + upsert + fetch are proven via the official MCP server; parsing the similarity-ranking response is pending, so retrieval would run fully inside TigerGraph.
- **Load the full 590K-transaction corpus into Savanna**: currently a 10K live subset plus the full graph in-memory.
- **Interactive evidence gathering**: Real customer/analyst responses instead of simulated ones.
- **Streaming investigation visualization**: Real-time updates as the investigation progresses.
- **Multi-agent collaboration**: Separate agents for different investigation phases, coordinated through a supervisor.

### Tech Stack

- **Graph Database**: TigerGraph Savanna (11 installed GSQL queries) + in-memory engine for the full 590K-transaction local graph
- **MCP**: Official `tigergraph-mcp` (69 tools, verified live) + 15 in-process investigation tools
- **LLM**: nvidia/nemotron-3-super-120b-a12b via OpenRouter, temperature 0, cached responses
- **Agent Framework**: Custom Python orchestrator
- **Web Dashboard**: FastAPI + vanilla HTML/JS
- **Data Models**: Pydantic for type-safe investigation state
- **Dataset**: IEEE-CIS Fraud Detection (590K transactions, 144K identity records, 5,565 closed cases)

### Try It

```bash
# Get the dataset: download the HHGOA_IEEE files into data/HHGOA_IEEE/
# (see the repo README)

# Run all 20 investigations
python main.py investigate

# Start the dashboard
python main.py serve
```

---

*Built for HHGOA 2026 — TigerGraph Partner Challenge*
