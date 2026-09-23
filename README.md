# 🕵️ Agentic Fraud Investigation Agent — TigerGraph + AI

**HHGOA 2026 — TigerGraph Partner Challenge**

An AI-powered fraud investigation agent that uses TigerGraph for graph-based evidence gathering, pattern detection, and case memory. The agent investigates fraud alerts, creates and progresses cases, recommends next-best-actions, and explains its reasoning — all under uncertainty.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    Fraud Investigation Agent                      │
│                                                                   │
│  ┌─────────┐    ┌──────────────┐    ┌───────────────────────┐   │
│  │  Trigger │───▶│  Orchestrator │───▶│  Policy Engine (R1-10)│   │
│  │  (Alert) │    │  (8-step flow)│    │  + SAR Generator      │   │
│  └─────────┘    └──────┬───────┘    └───────────────────────┘   │
│                        │                                          │
│         ┌──────────────┼──────────────┐                          │
│         ▼              ▼              ▼                          │
│  ┌─────────────┐ ┌──────────┐ ┌──────────────┐                 │
│  │   Evidence   │ │ Pattern  │ │  GraphRAG    │                 │
│  │   Gatherer   │ │ Detector │ │  Context     │                 │
│  └──────┬──────┘ └────┬─────┘ └──────┬───────┘                 │
│         │              │              │                          │
│         └──────────────┼──────────────┘                          │
│                        ▼                                          │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │              TigerGraph (In-Memory / Savanna)                ││
│  │  Customer ──OWNS──▶ Card ──MADE──▶ Transaction               ││
│  │  Transaction ──FROM_DEVICE──▶ DeviceProfile                  ││
│  │  Transaction ──BILLED_IN──▶ BillingRegion                    ││
│  │  ClosedCase ──INVOLVES──▶ Transaction                       ││
│  │  InvestigationCase ──links──▶ all entities                  ││
│  └─────────────────────────────────────────────────────────────┘│
│                        │                                          │
│                        ▼                                          │
│  ┌─────────────────────────────────────────┐                    │
│  │         MCP Server (Tools API)           │                    │
│  │  get_card_transactions, detect_patterns  │                    │
│  │  find_shared_devices, write_case, ...    │                    │
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
- In-memory graph engine for local development; compatible with TigerGraph Savanna/CE
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
- 8-step investigation cycle: Trigger → Investigate → Gather → Assess → More Evidence → Actions → Explain → Memory
- Evidence requests with simulated responses
- Case creation and graph writes for case memory

### 6. TigerGraph MCP Server (`src/mcp/server.py`)
- 15 tools exposing graph operations
- OpenAI function-calling compatible format
- Tool calls for: transactions, devices, patterns, velocity, closed cases

### 7. GraphRAG Context (`src/evidence/graphrag.py`)
- Synthesizes graph evidence + policy rules for LLM grounding
- Structured context sections for investigation reasoning

### 8. Web Dashboard (`src/ui/app.py`)
- Dark-mode analyst interface
- Case list with verdict/probability/pattern
- Detailed case view with tabs: Summary, Evidence, Actions, SAR, Case Memory
- Real-time graph statistics

## Quick Start

### Prerequisites
- Python 3.11+
- Dataset files in `data/HHGOA_IEEE/` (see dataset section below)

### Install
```bash
pip install -r requirements.txt
```

### Run Investigation (all 20 cases)
```bash
python main.py investigate
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
├── tigergraph/
│   └── schema.gsql                  # TigerGraph GSQL schema
├── src/
│   ├── utils/
│   │   ├── config.py                # Configuration from env
│   │   └── models.py                # Pydantic data models
│   ├── graph/
│   │   ├── in_memory_graph.py       # In-memory graph engine
│   │   └── data_loader.py           # CSV → graph loader
│   ├── evidence/
│   │   ├── gatherer.py              # Evidence collection from graph
│   │   ├── pattern_detector.py      # Fraud pattern detection
│   │   └── graphrag.py              # GraphRAG context synthesis
│   ├── policy/
│   │   └── engine.py                # Policy rules R1-R10
│   ├── agent/
│   │   └── orchestrator.py          # Investigation orchestrator
│   ├── mcp/
│   │   └── server.py                # TigerGraph MCP tools
│   └── ui/
│       └── app.py                   # FastAPI web dashboard
├── cases/                           # Output: 20 answer JSONs
├── data/
│   └── HHGOA_IEEE/                 # Dataset files
├── tests/                           # Test suite
└── docs/                            # Documentation
```

## Answer Format

Each case produces a JSON file with three parts:
1. **Case**: Internal investigation record with evidence, findings, verdict
2. **SAR**: Suspicious Activity Report (when policy requires)
3. **Next Best Actions**: Initial and final recommendations with approval routes

See `data/HHGOA_IEEE/README.md` for the complete specification.

## TigerGraph Usage

### Schema Design
The graph schema models the financial entity relationships:
- **Customer** → OWNS → **Card** → MADE → **Transaction**
- **Transaction** → FROM_DEVICE → **DeviceProfile** (online only)
- **Transaction** → BILLED_IN → **BillingRegion**
- **ClosedCase** → INVOLVES → **Transaction**
- **InvestigationCase** → links to all relevant entities

### Graph Algorithms Used
- **Breadth-first traversal**: Card → Transaction → Device → Other Cards
- **Pattern matching**: Card testing sequences, velocity spikes
- **Community detection**: Shared device profiles across cards
- **Similarity search**: Matching current case to closed cases

### Case Memory
Each completed investigation is written back to the graph as an `InvestigationCase` vertex with edges to all relevant entities. Future investigations retrieve similar cases through graph queries.

## Agentic Capabilities

1. **Multi-step investigation**: 8-step cycle with evidence gathering and reassessment
2. **Tool use**: 15 MCP tools for graph operations
3. **Memory**: Case memory through graph writes and retrieval
4. **Policy compliance**: R1-R10 rule enforcement with approval routing
5. **Uncertainty handling**: Calibrated probability with stopping rules
6. **Evidence requests**: Customer verification, step-up auth simulation
7. **Explainability**: Evidence-based reasoning with citations

## What We Learned

1. **Graph-native investigation is powerful**: Tracing money through shared devices, regions, and cards reveals patterns invisible to row-based analysis
2. **Calibration matters**: An agent that blocks everything scores badly — half the cases are legitimate
3. **Case memory is a multiplier**: Prior investigations become evidence for future ones
4. **Policy compliance requires careful engineering**: The gap between "recommend" and "execute" must be enforced

## What We'd Improve

- Real TigerGraph Savanna integration with GSQL queries
- LLM-powered reasoning with GraphRAG context (currently rule-based)
- Interactive evidence gathering with real customer/analyst responses
- Streaming investigation visualization
- Multi-agent collaboration for complex fraud rings
