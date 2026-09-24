#!/usr/bin/env python3
"""RPM pacer patch — lets provider=openai respect free-tier limits (e.g. Cerebras gpt-oss-120b: 5 RPM).

Adds optional env vars to .env:
    OPENAI_RPM=5        # 0 or unset = no pacing (evaluator default)
    OPENAI_RPD=240      # optional daily cap
Idempotent.
"""
import sys
from pathlib import Path

TARGET = Path(__file__).parent.parent / "src" / "agent" / "llm_reasoner.py"

OLD = '''            self._client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            self.model = self.model or "gpt-4o"
            logger.info("OpenAI: %s (no rate limit, cached)", self.model)
'''
NEW = '''            self._client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            self.model = self.model or "gpt-4o"
            rpm = int(os.getenv("OPENAI_RPM", "0") or 0)
            if rpm > 0:
                self._rate_limiter = FreeTierRateLimiter(
                    rpm, int(os.getenv("OPENAI_RPD", "100000") or 100000))
                logger.info("OpenAI: %s (client-side pacer: %d RPM, cached)",
                            self.model, rpm)
            else:
                logger.info("OpenAI: %s (no rate limit, cached)", self.model)
'''


def main():
    src = TARGET.read_text(encoding="utf-8")
    if "OPENAI_RPM" in src:
        print("Already patched — nothing to do.")
        return
    if src.count(OLD) != 1:
        print("ERROR: could not locate the _setup_openai block — aborting.")
        sys.exit(1)
    TARGET.write_text(src.replace(OLD, NEW, 1), encoding="utf-8")
    import py_compile
    py_compile.compile(str(TARGET), doraise=True)
    print("Patched src/agent/llm_reasoner.py (OPENAI_RPM pacer) — compiles OK.")
    print("Add to .env:  OPENAI_RPM=5   (and optionally OPENAI_RPD=240)")
    print("Then: python scripts\\check_llm.py   ->   python main.py investigate")


if __name__ == "__main__":
    main()