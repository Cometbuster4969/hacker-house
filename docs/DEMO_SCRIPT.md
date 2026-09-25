# Demo video script (≈3 min)

| Time | Screen | Voice-over |
|---|---|---|
| 0:00 | README results table | "Twenty alerts. Half are legitimate. The risk score alone gets most of them wrong. Here's an agent that investigates instead of guessing." |
| 0:15 | `python main.py serve` → dashboard, case table | "Every case: a verdict, a calibrated probability, the fraud episode, and the actions Fraud Policy v1.0 requires." |
| 0:35 | HHG-006 → Agent Trace tab | "Four purchases in thirty minutes, each just under five hundred dollars, all different amounts. The agent sweeps the population, finds the same shape on three other customers' cards, and matches five confirmed closed cases. Undocumented structuring, so R9: file a report and escalate." |
| 1:05 | HHG-014 trace, device_neighbors | "One Samsung device, new for every account and behind an anonymous proxy, on twenty cards of twenty customers. Shared origin, R6: monitor the nineteen connected cards." |
| 1:25 | HHG-017 | "A hundred-dollar charge with a 0.57 risk score. Our model, trained on the bank's own outcomes, scores it 0.01, and nothing else on the card looks off. Legitimate, case closed." |
| 1:40 | HHG-004 evidence_requests | "When a customer disputes a charge the evidence calls ordinary, we don't pretend. The case stays uncertain and goes to a human, R8." |
| 1:55 | Backtest panel | "Replaying 544 October closed cases through the whole agent, trained only on earlier months: 82% verdict accuracy on decided cases, blocks on 8% of cleared cases, 91% SAR agreement." |
| 2:20 | Monitoring panel | "Then it keeps watching. With no case pack it opened fifteen alerts of its own, including the full twenty-eight-customer device ring." |
| 2:40 | `tigergraph/queries/engine_queries.gsql`, `python -m src.mcp.engine_server` | "Every tool is an as-of-bounded GSQL query, also exposed over MCP. The policy engine is a pure function, pinned by tests and a validator: twenty files, zero violations." |
