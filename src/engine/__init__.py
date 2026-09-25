"""Anvesh v2 investigation engine (merged repo + zip solution).

Layers (hard boundaries):
  L1 Evidence   - graph / store queries, all bounded by the case's as_of time
  L2 Judgement  - calibrated transaction model + episode/pattern/ring detectors + case memory
  L3 Decision   - pure policy engine (R1-R10, routes computed never generated)
  L4 Explanation- narrative writer (deterministic; optional LLM polish, validated)
"""
