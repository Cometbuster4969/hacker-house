# Building an Agentic Fraud Investigation Agent with TigerGraph

## HHGOA 2026 — TigerGraph Partner Challenge

### The Problem

Fraud analysts at financial institutions face an impossible workload. Each alert requires manually gathering transaction history, tracing money movement across accounts, identifying connected devices and cards, reviewing policies, assessing risk, and deciding what action to take — all under time pressure. By the time the investigation is complete, the money is often already gone.

We built an **AI-powered Fraud Investigation Agent** that automates this entire investigation lifecycle, powered by **TigerGraph** for graph-based evidence gathering and case memory.

### What We Built

Our agent takes a fraud alert — triggered by a risk score, customer report, or analyst request — and runs a complete 8-step investigation:

1. **Trigger**: Accept the alert and identify the flagged transaction
2. **Investigate**: Query the knowledge graph for transaction history, device connections, billing regions, and email domains
3. **Gather Evidence**: Collect structured evidence from graph traversals — card velocity, shared devices, connected cards, region patterns
4. **Assess Uncertainty**: Detect fraud patterns (5 known + undocumented) and calculate calibrated probability
5. **Gather More Evidence**: Request customer verification or step-up authentication when signals are ambiguous
6. **Take Actions**: Recommend next-best-actions with proper approval routing (auto/L1/L2)
7. **Explain**: Generate evidence-based reasoning with policy rule citations
8. **Update Memory**: Write the completed case back to the graph for future investigations

### Architecture

```
┌─────────────────────────────────────────────┐
│           Fraud Investigation Agent          │
│                                              │
│  Trigger → Orchestrator → Policy Engine      │
│                ↓                              │
│  Evidence Gatherer + Pattern Detector         │
│                ↓                              │
│  TigerGraph (In-Memory / Savanna)            │
│  8 vertex types, 20 edge types               │
│                ↓                              │
│  MCP Server (15 tools)                       │
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

#### Graph Algorithms
- **Breadth-first traversal**: From a flagged transaction → card → all card transactions → devices → other cards on same devices
- **Pattern matching**: Card testing detection (small authorizations → large purchase), out-of-region use, new device detection
- **Community detection**: Finding shared device profiles across multiple cards (fraud ring indicators)
- **Similarity search**: Matching current investigation signals to closed cases in the graph

#### TigerGraph MCP
We built an MCP server exposing 15 graph operations as tools:
- `get_card_transactions`, `get_customer_cards`, `get_device_profile`
- `detect_card_testing`, `detect_out_of_region`, `get_card_velocity`
- `find_shared_devices`, `find_similar_closed_cases`
- `write_investigation_case` (for case memory)

These tools can be called by any LLM agent framework through the Model Context Protocol.

#### Case Memory
Each completed investigation is written back to the graph as an `InvestigationCase` vertex with edges to all relevant entities. When a new investigation starts, the agent queries for similar past cases through graph traversal. This creates a feedback loop where investigations improve over time.

### Agentic Capabilities

1. **Multi-step investigation workflow**: The agent follows an 8-step cycle, not a single-shot classification
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

- **Real TigerGraph Savanna integration**: Our current implementation uses an in-memory graph engine. Connecting to TigerGraph Savanna with GSQL queries would unlock more sophisticated graph algorithms.
- **LLM-powered reasoning**: Currently using rule-based assessment. Adding an LLM with GraphRAG context would enable more nuanced reasoning about ambiguous cases.
- **Interactive evidence gathering**: Real customer/analyst responses instead of simulated ones.
- **Streaming investigation visualization**: Real-time updates as the investigation progresses.
- **Multi-agent collaboration**: Separate agents for different investigation phases, coordinated through a supervisor.
- **Vector similarity search**: Using TigerGraph's vector store for semantic search over case narratives and policy documents.

### Tech Stack

- **Graph Database**: TigerGraph (in-memory engine, compatible with Savanna/CE)
- **Graph Query**: GSQL-compatible operations via MCP
- **Agent Framework**: Custom Python orchestrator
- **Web Dashboard**: FastAPI + vanilla HTML/JS
- **Data Models**: Pydantic for type-safe investigation state
- **Dataset**: IEEE-CIS Fraud Detection (590K transactions, 144K identity records, 5,565 closed cases)

### Try It

```bash
# Get the dataset: download the HHGOA_IEEE files into data/HHGOA_IEEE/
# (see the repo README). If you only want a quick smoke test without the
# 708 MB download, generate synthetic stand-in data instead:
#   python scripts/generate_demo_data.py
# (it refuses to overwrite real dataset files)

# Run all 20 investigations
python main.py investigate

# Start the dashboard
python main.py serve
```

---

*Built for HHGOA 2026 — TigerGraph Partner Challenge*
