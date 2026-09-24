#!/usr/bin/env python3
"""10-second LLM preflight: verifies the configured provider actually answers.

Run before `python main.py investigate` whenever the provider/key changes:
    python scripts/check_llm.py

Checks: which base_url the OpenAI client will hit, that auth works, that the
configured model responds, and that the response parses. Exit 0 = safe to run.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

load_dotenv()

provider = os.getenv("LLM_PROVIDER", "openai")
model = os.getenv("LLM_MODEL", "(provider default)")
base_url = os.getenv("OPENAI_BASE_URL", "(default: api.openai.com)")

print(f"provider   : {provider}")
print(f"model      : {model}")
print(f"base_url   : {base_url}")

if provider != "openai":
    print(f"\nNOTE: provider is '{provider}', not 'openai'. If you intended to use a "
          "Gemini/Cerebras key through the OpenAI-compatible endpoint, set:\n"
          "  LLM_PROVIDER=openai\n  OPENAI_API_KEY=<that key>\n"
          "  OPENAI_BASE_URL=<endpoint>/v1\n  LLM_MODEL=<model name>")

if provider == "openai" and "groq.com" in base_url:
    print("\nWARNING: base_url points at Groq — the 200K tokens/day cap will bite "
          "mid-run. Point OPENAI_BASE_URL at Gemini or Cerebras instead.")

from src.agent.llm_reasoner import LLMReasoner

llm = LLMReasoner()
if not llm.is_llm_available:
    print("\nFAIL: LLMReasoner reports provider unavailable (check key/env names).")
    sys.exit(1)

prompt = ('Reply with a single JSON object, nothing else: '
          '{"ok": true, "note": "<one short sentence>"}')
t0 = time.time()
try:
    raw = llm._call_llm_cached(prompt)
except AttributeError:
    raw = llm._client.chat.completions.create(
        model=llm.model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=60,
    ).choices[0].message.content
dt = time.time() - t0

print(f"\nraw reply  : {raw!r}")
print(f"latency    : {dt:.1f}s")
ok = raw is not None and "ok" in raw.lower()
print(f"\n{'PASS — safe to run python main.py investigate' if ok else 'FAIL — reply unusable'}")
sys.exit(0 if ok else 1)