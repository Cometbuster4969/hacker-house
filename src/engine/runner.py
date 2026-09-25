"""Run the agent over the case pack (in opened_at order, so memory flows forward)."""
from __future__ import annotations

import json
import logging

import pandas as pd

from . import config
from .memory import CaseMemory
from .store import InvestigationStore, load_case_pack, load_closed_cases

log = logging.getLogger(__name__)


def load_store() -> InvestigationStore:
    df = pd.read_parquet(config.STORE_DIR / "scored.parquet")
    return InvestigationStore(df, load_closed_cases())


def graph_writer_from_env():
    from src.utils.config import TG_HOST, TG_TOKEN, TG_GRAPH  # noqa: WPS433
    if not TG_HOST:
        return None
    try:
        from src.graph.tigergraph_client import TigerGraphClient
        cli = TigerGraphClient(host=TG_HOST, token=TG_TOKEN, graph=TG_GRAPH)
        if not cli.connect():
            return None
    except Exception as e:  # noqa: BLE001
        log.warning("TigerGraph unavailable (%s); cases stay in the memory file", e)
        return None

    def write(rec: dict) -> bool:
        return cli.upsert_agent_case(rec)
    return write


def run(case_ids: list[str] | None = None, out_dir=None, store: InvestigationStore | None = None) -> list[dict]:
    from .investigator import Investigator
    out_dir = out_dir or config.CASES_DIR
    store = store or load_store()
    mem = CaseMemory(store.closed)
    agent = Investigator(store, mem, graph_writer_from_env())
    cp = load_case_pack().sort_values("opened_at")
    answers = []
    config.TRACES_DIR.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    for _, case in cp.iterrows():
        ans, trace = agent.investigate(case)
        if case_ids and case.case_id not in case_ids:
            continue      # still investigated so memory is identical to a full run
        (out_dir / f"{case.case_id}.json").write_text(json.dumps(ans, indent=2, default=str))
        (config.TRACES_DIR / f"{case.case_id}.json").write_text(json.dumps(trace, indent=2, default=str))
        c = ans["case"]
        log.info("%s %-10s p=%.2f %-28s exp=%9.2f final=%s", case.case_id, c["verdict"], c["fraud_probability"],
                 c["pattern"], c["exposure_usd"], ",".join(a["action"] for a in ans["next_best_actions"]["final"]))
        answers.append(ans)
    if not case_ids:
        rows = [{"case_id": a["case_id"], "verdict": a["case"]["verdict"], "p": a["case"]["fraud_probability"],
                 "status": a["case"]["status"], "pattern": a["case"]["pattern"], "exposure_usd": a["case"]["exposure_usd"],
                 "n_affected": len(a["case"]["affected_txn_ids"]), "n_connected_cards": len(a["case"]["connected_card_ids"]),
                 "evidence_request": (a["evidence_requests"][0]["type"] if a["evidence_requests"] else ""),
                 "final": [x["action"] for x in a["next_best_actions"]["final"]], "sar": a["sar"]["file"],
                 "tool_calls": a["tool_calls"], "tokens": a["tokens"], "latency_s": a["latency_s"]}
                for a in sorted(answers, key=lambda a: a["case_id"])]
        summ = {"engine": "src/engine", "n_cases": len(rows),
                "verdicts": {v: sum(r["verdict"] == v for r in rows) for v in ("fraud", "legitimate", "uncertain")},
                "sars": sum(r["sar"] for r in rows), "tokens": sum(r["tokens"] for r in rows),
                "tool_calls": sum(r["tool_calls"] for r in rows),
                "written_to_graph": sum(a["case"]["written_to_graph"] for a in answers), "cases": rows}
        config.BENCH_DIR.mkdir(exist_ok=True)
        (config.BENCH_DIR / "run_summary.json").write_text(json.dumps(summ, indent=2))
    return answers
