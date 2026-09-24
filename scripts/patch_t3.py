#!/usr/bin/env python3
"""T3 patch — bounded-move reassessment in src/agent/orchestrator.py.

Fixes the Step-7 ratchet that inflated probabilities to 1.0 (17/20 fraud).
Idempotent: run once, then `python main.py investigate` to regenerate answers.
"""
import re
import sys
from pathlib import Path

TARGET = Path(__file__).parent.parent / "src" / "agent" / "orchestrator.py"

OLD_PATTERN = re.compile(
    r"[ \t]*# Reassess after evidence request\n"
    r".*?state\.fraud_probability = self\.pattern_detector\.calculate_fraud_probability\(state\)\n"
    r".*?logger\.info\(\"LLM reassessment: prob=%.2f, verdict=%s\",\n"
    r"\s*state\.fraud_probability, state\.verdict\.value\)\n",
    re.DOTALL,
)

NEW_BLOCK = '''            # Reassess after evidence request.
            # T3 fix — bounded-move reassessment: recomputing the raw rule score on
            # LLM-amended state saturated at 1.0 and dragged the numeric-lockdown
            # band up with it (caused 17/20 fraud over-blocking). The anchor stays
            # at the Step-6 assessed probability; reassessment may move it ±0.15 max.
            anchor = state.fraud_probability
            recalc = self.pattern_detector.calculate_fraud_probability(state)
            lo = max(0.0, anchor - 0.15)
            hi = min(1.0, anchor + 0.15)
            if self.llm.is_llm_available:
                reassessment = self.llm.assess_evidence(state, graph_context, anchor)
                new_prob = reassessment.get("fraud_probability")
                if new_prob is not None:
                    state.fraud_probability = max(lo, min(hi, float(new_prob)))
                if reassessment.get("verdict"):
                    try:
                        state.verdict = Verdict(reassessment["verdict"])
                    except ValueError:
                        pass
                logger.info(
                    "LLM reassessment: prob=%.2f (anchor=%.2f, rule-recalc=%.2f), verdict=%s",
                    state.fraud_probability, anchor, recalc, state.verdict.value)
            else:
                state.fraud_probability = max(lo, min(hi, recalc))

            # T3: verdict/probability consistency guard
            if state.verdict == Verdict.FRAUD and state.fraud_probability < 0.50:
                state.verdict = Verdict.UNCERTAIN
            elif state.verdict == Verdict.LEGITIMATE and state.fraud_probability > 0.60:
                state.verdict = Verdict.UNCERTAIN
'''


def main():
    src = TARGET.read_text(encoding="utf-8")
    if "bounded-move reassessment" in src:
        print("Already patched — nothing to do.")
        return
    new_src, n = OLD_PATTERN.subn(NEW_BLOCK, src, count=1)
    if n != 1:
        print("ERROR: could not locate the Step-7 reassessment block. "
              "Has orchestrator.py been modified? Aborting.")
        sys.exit(1)
    TARGET.write_text(new_src, encoding="utf-8")
    import py_compile
    py_compile.compile(str(TARGET), doraise=True)
    print("Patched src/agent/orchestrator.py (Step-7 bounded-move reassessment) — compiles OK.")
    print("Next: python main.py investigate   (regenerates all 20 case answers)")


if __name__ == "__main__":
    main()