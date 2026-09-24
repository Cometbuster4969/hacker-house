# LOCAL SETUP GUIDE
# Everything you need to do that I can't do from the sandbox.
# Follow these steps in order. Each one is necessary.

## ========================================
## STEP 1: DOWNLOAD THE REAL DATASET
## ========================================

# The real dataset is ~730MB total. I couldn't download it from the sandbox
# (Google Drive SSL issues). You need to download it manually.

# Go to: https://drive.google.com/drive/folders/1YDJUW1fiE7Jx8R9KqknC4IcsED9zll2A

# Download these 5 files:
#   1. transactions.csv      (~708 MB) - 590,742 transactions
#   2. identity.csv           (~25 MB) - 144,432 identity records
#   3. closed_cases_history.csv (~2.6 MB) - 5,565 closed cases
#   4. case_pack.csv          (~3 KB) - 20 benchmark cases
#   5. README.md              (~38 KB) - Full dataset documentation

# Place them in: data/HHGOA_IEEE/
# The repo already has a README.md and synthetic case_pack.csv there.
# OVERWRITE them with the real files.

# Verify:
ls -la data/HHGOA_IEEE/
# Should show:
#   transactions.csv        ~708 MB
#   identity.csv            ~25 MB
#   closed_cases_history.csv ~2.6 MB
#   case_pack.csv           ~3 KB
#   README.md               ~38 KB


## ========================================
## STEP 2: SET UP TIGERGRAPH
## ========================================

# Option A: TigerGraph Savanna (recommended, cloud, free)
# -----------------------------------------------
# 1. Go to https://savanna.tgcloud.io
# 2. Sign up / log in
# 3. Create a new workspace
# 4. Name it: FraudInvestigation
# 5. IMPORTANT: Enable auto-stop and auto-start
# 6. Wait for it to be ready (~2-3 minutes)
# 7. Go to Admin → Tokens → Generate a token
# 8. Copy the token and workspace URL

# Option B: TigerGraph Community Edition (local Docker)
# -----------------------------------------------
# docker run -d --name tigergraph \
#   -p 14022:22 -p 14240:14240 -p 9000:9000 \
#   -v tigergraph_data:/home/tigergraph \
#   tigergraph/tigergraph:latest
#
# Wait ~60 seconds for startup, then:
# docker exec -it tigergraph bash
# gsql 'CREATE GRAPH FraudInvestigation()'
# gsql 'USE GRAPH FraudInvestigation'


## ========================================
## STEP 3: DEPLOY THE GRAPH SCHEMA
## ========================================

# The schema is at: tigergraph/schema.gsql

# Option A: Via Savanna UI
# 1. Go to your Savanna workspace
# 2. Open GSQL Editor
# 3. Copy-paste the contents of tigergraph/schema.gsql
# 4. Run it

# Option B: Via command line (CE)
# docker exec -it tigergraph gsql
# > DROP GRAPH FraudInvestigation IF EXISTS;
# > (paste contents of tigergraph/schema.gsql)
# > INSTALL QUERY ALL
# > GSQL_HOME=/home/tigergraph/tigergraph

# Option C: Via Python script (after installing pyTigerGraph)
python scripts/deploy_schema.py


## ========================================
## STEP 4: LOAD DATA INTO TIGERGRAPH
## ========================================

# This is the most important step. The data needs to be in TigerGraph
# for the graph queries to work.

# Option A: Use the loading script
python scripts/load_to_tigergraph.py

# Option B: Manual via GSQL (for smaller datasets or testing)
# Use the loading jobs defined in the schema


## ========================================
## STEP 5: SET UP YOUR LLM API KEY
## ========================================

# This is what makes it a HYBRID agent (rules + AI reasoning).
# Without this, it runs in rule-based mode only.

# ──────────────────────────────────────────────────────────────
# Option A: OpenRouter FREE TIER (you have this — use it)
# ──────────────────────────────────────────────────────────────
# 1. Go to https://openrouter.ai/keys
# 2. Copy your API key
# 3. Set environment variables:

export OPENROUTER_API_KEY="sk-or-your-key-here"
export LLM_PROVIDER="openrouter"
export LLM_MODEL="llama-3.3-70b"

# RECOMMENDED FREE MODELS (ordered by quality for fraud analysis):
#
#   Model ID                              Short name        Quality   Speed
#   ────────────────────────────────────────────────────────────────────────
#   meta-llama/llama-3.3-70b-instruct:free   llama-3.3-70b     ★★★★☆   Fast
#   nvidia/nemotron-3-super-120b-a12b:free   nemotron-120b     ★★★★★   Slow
#   qwen/qwen3-next-80b-a3b-instruct:free    qwen3-80b         ★★★★☆   Medium
#   meta-llama/llama-4-maverick:free          llama-4-maverick  ★★★★☆   Medium
#   google/gemma-4-31b-it:free                gemma-4-31b       ★★★☆☆   Fast
#   openai/gpt-oss-120b:free                  gpt-oss-120b      ★★★★☆   Medium
#
# BEST PICK: llama-3.3-70b (best balance of quality + speed + availability)
# BEST QUALITY: nemotron-120b (slower but strongest reasoning)
#
# RATE LIMITS (free tier):
#   - 20 requests per minute (hard cap)
#   - 50 requests per day (no credits)
#   - 1,000 requests per day (after $10 lifetime credits)
#
# For 20 cases × ~4 LLM calls each = ~80 calls total
# With 50/day limit: takes ~2 days
# With $10 credits (1000/day): takes ~5 minutes
#
# The agent has built-in rate limiting and will auto-rotate between
# free models if one hits the limit.

# ──────────────────────────────────────────────────────────────
# Option B: OpenAI (NO rate limiting — for evaluators)
# ──────────────────────────────────────────────────────────────
# export OPENAI_API_KEY="sk-your-key-here"
# export LLM_PROVIDER="openai"
# export LLM_MODEL="gpt-4o"

# ──────────────────────────────────────────────────────────────
# Option C: Anthropic (NO rate limiting — for evaluators)
# ──────────────────────────────────────────────────────────────
# export ANTHROPIC_API_KEY="sk-ant-your-key-here"
# export LLM_PROVIDER="anthropic"
# export LLM_MODEL="claude-sonnet-4-20250514"

# ──────────────────────────────────────────────────────────────
# Option D: No LLM (rule-based only)
# ──────────────────────────────────────────────────────────────
# Don't set any API key. The agent uses rules only.
# This works but won't have LLM-powered pattern detection or explanations.


## ========================================
## STEP 6: CREATE YOUR .ENV FILE
## ========================================

# Copy .env.example to .env and fill in your values:
cp .env.example .env

# Edit .env:
cat > .env << 'EOF'
# TigerGraph
TIGERGRAPH_HOST=https://YOUR_WORKSPACE.tgcloud.io
TIGERGRAPH_TOKEN=your_token_here
TIGERGRAPH_GRAPH=FraudInvestigation
TIGERGRAPH_USE_SAVANNA=true

# LLM — OpenRouter free tier (your key)
OPENROUTER_API_KEY=sk-or-your-key-here
LLM_PROVIDER=openrouter
LLM_MODEL=llama-3.3-70b

# For evaluators, uncomment ONE of these instead:
# OPENAI_API_KEY=sk-your-key-here
# ANTHROPIC_API_KEY=sk-ant-your-key-here

# App
DATA_DIR=./data/HHGOA_IEEE
CASES_DIR=./cases
LOG_LEVEL=INFO
PORT=8000
EOF


## ========================================
## STEP 7: INSTALL DEPENDENCIES
## ========================================

pip install -r requirements.txt

# Key packages:
#   pyTigerGraph    - TigerGraph Python client
#   openai          - OpenAI API client (or anthropic)
#   fastapi         - Web dashboard
#   pydantic        - Data models
#   uvicorn         - Web server


## ========================================
## STEP 8: RUN THE INVESTIGATION
## ========================================

# Run all 20 cases (this is the main deliverable):
python main.py investigate

# Run a single case (for testing):
python main.py investigate HHG-001

# The output goes to: cases/HHG-001.json through HHG-020.json


## ========================================
## STEP 9: VERIFY THE OUTPUT
## ========================================

# Check that all 20 cases were processed:
ls cases/*.json | wc -l
# Should show: 20

# Check the verdicts:
python3 -c "
import json
from pathlib import Path
for f in sorted(Path('cases').glob('*.json')):
    d = json.load(open(f))
    c = d['case']
    print(f\"{d['case_id']}: {c['verdict']:>12} | {c['fraud_probability']:.2f} | {c['pattern']:<30} | SAR: {d['sar']['file']}\")
"

# Verify format matches spec:
python -m pytest tests/test_investigation.py -v


## ========================================
## STEP 10: START THE DASHBOARD
## ========================================

python main.py serve
# Open http://localhost:8000

# You should see:
# - Summary stats bar (Total/Fraud/Legit/Uncertain/SARs)
# - Case list table with verdict, probability, pattern, exposure
# - Click any case to see: Summary, Evidence, Actions, SAR, Case Memory tabs


## ========================================
## STEP 11: TUNE THE AGENT (IMPORTANT!)
## ========================================

# The real dataset will behave differently from the synthetic demo data.
# You need to tune thresholds based on what you see.

# Key tuning points in the code:

# 1. Pattern detection thresholds (src/evidence/pattern_detector.py)
#    - Card testing: "amount < 5.0" for small txns, ">= 50.0" for large
#    - CNP fraud: "amount > avg * 3 and amount > 100"
#    - These may need adjustment for the real data

# 2. Fraud probability calculation (src/evidence/pattern_detector.py)
#    - calculate_fraud_probability() - the weights for each signal
#    - Customer report boost: +0.15
#    - High risk score boost: +0.05
#    - Confirmed fraud case boost: +0.10

# 3. Policy thresholds (src/policy/engine.py)
#    - THRESHOLDS dict at the top
#    - verify_below: 0.70 (R1 threshold)
#    - high_confidence: 0.85 (stopping rule)
#    - low_confidence: 0.15 (stopping rule)
#    - sar_exposure: 1000 (SAR filing threshold)

# 4. LLM prompts (src/agent/llm_reasoner.py)
#    - SYSTEM_PROMPT - the analyst persona
#    - assess_evidence() - the assessment prompt
#    - determine_actions() - the action recommendation prompt
#    - These are the most impactful tuning points for LLM mode

# 5. Evidence simulation (src/agent/orchestrator.py)
#    - _simulate_customer_response() - how we simulate customer replies
#    - Customer reports → always deny (they said "I never made this")
#    - High risk → likely deny
#    - Low risk → likely confirm


## ========================================
## STEP 12: CUSTOMIZE FOR BETTER SCORES
## ========================================

# The judging criteria are:
#   Investigation accuracy: 25%  (pattern detection quality)
#   Next best action: 25%        (action recommendations)
#   Case summary: 10%            (explainability)
#   Agentic design: 15%          (architecture, tool use, memory)
#   Innovation: 15%              (graph usage, GraphRAG)
#   Demo: 10%                    (presentation)

# To improve:

# 1. Investigation accuracy:
#    - Run on real data, check which cases are wrong
#    - Adjust pattern detection thresholds
#    - Improve the LLM assessment prompts
#    - Add more graph queries for evidence

# 2. Next best action:
#    - Ensure policy rules are correctly applied
#    - Make sure initial vs final actions differ when evidence is requested
#    - Verify approval routes are correct

# 3. Case summary:
#    - The LLM-generated summaries should be clear and specific
#    - Include transaction IDs, amounts, dates
#    - Cite policy rules

# 4. Agentic design:
#    - The 8-step cycle is already solid
#    - Add more MCP tools if needed
#    - Improve case memory retrieval

# 5. Innovation:
#    - The GraphRAG context synthesis is a strong point
#    - Document the graph algorithms used
#    - Show how case memory improves over time


## ========================================
## STEP 13: PREPARE SUBMISSION
## ========================================

# 1. Verify all 20 case files exist and are valid
# 2. Run the dashboard for the demo
# 3. Record a 3-5 minute demo video showing:
#    - Loading data into TigerGraph
#    - Running the investigation
#    - Showing the dashboard with results
#    - Explaining the architecture
# 4. Write the blog post (docs/BLOG_POST.md has a draft)
# 5. Post on social media (docs/SOCIAL_POST.md has drafts)
# 6. Submit at: https://forms.gle/yxXzqSULGgZ9VUF56


## ========================================
## TROUBLESHOOTING
## ========================================

# Problem: "No TigerGraph host configured"
# Solution: Set TIGERGRAPH_HOST in .env, or ignore if using in-memory mode

# Problem: "LLM call failed"
# Solution: Check your API key, or set LLM_PROVIDER=mock for rule-based mode

# Problem: "No case pack loaded"
# Solution: Make sure case_pack.csv is in data/HHGOA_IEEE/

# Problem: Wrong verdicts on real data
# Solution: Tune thresholds in src/evidence/pattern_detector.py and src/policy/engine.py

# Problem: Dashboard shows no cases
# Solution: Run `python main.py investigate` first, then `python main.py serve`

# Problem: TigerGraph connection fails
# Solution: Check your token, make sure the workspace is running (not auto-stopped)
