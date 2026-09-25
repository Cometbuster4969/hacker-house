#!/usr/bin/env python3
"""
Fraud Investigation Agent — Main Entry Point

Modes:
    python main.py build                Build the transaction store (data/store) from the dataset
    python main.py train                Train models + write benchmark/backtest.json
    python main.py investigate          Run all 20 cases with the engine (src/engine)
    python main.py investigate HHG-001  Write a single case (all cases still run, so memory is identical)
    python main.py validate             Check cases/ against the answer contract + policy
    python main.py backtest             Replay October closed cases end-to-end
    python main.py monitor              Autonomous sweep beyond the 20 cases -> monitoring/
    python main.py serve                Start the web dashboard
    python main.py investigate --legacy Run the original rules+LLM orchestrator (src/agent)

Environment:
    LLM_PROVIDER=openai     Use OpenAI for LLM reasoning
    LLM_PROVIDER=anthropic  Use Anthropic for LLM reasoning
    LLM_PROVIDER=mock       Use rule-based only (default, no API key needed)
    TIGERGRAPH_HOST=...     Connect to TigerGraph Savanna/CE
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.utils.config import (
    DATA_DIR, CASES_DIR, PORT, ensure_dirs,
    LLM_PROVIDER, LLM_MODEL, TG_HOST, TG_TOKEN, TG_GRAPH,
)
from src.utils.models import CasePackEntry
from src.graph.in_memory_graph import InMemoryGraph
from src.graph.data_loader import DataLoader
from src.agent.orchestrator import FraudInvestigationAgent
from src.mcp.server import TigerGraphMCPServer
from src.ui.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def load_data(graph: InMemoryGraph, max_transactions: int = 0) -> DataLoader:
    loader = DataLoader(str(DATA_DIR), graph)
    loader.load_all(max_transactions=max_transactions)
    return loader


def run_investigation(graph: InMemoryGraph, loader: DataLoader,
                      case_id: str = None):
    # Determine LLM mode
    llm_provider = LLM_PROVIDER
    llm_model = LLM_MODEL

    if llm_provider == "mock":
        logger.info("Mode: RULE-BASED (no LLM). Set LLM_PROVIDER=openai and "
                     "OPENAI_API_KEY for hybrid mode.")
    else:
        logger.info("Mode: HYBRID (rules + LLM). Provider: %s, Model: %s",
                     llm_provider, llm_model)

    agent = FraudInvestigationAgent(
        graph,
        llm_provider=llm_provider,
        llm_model=llm_model,
    )

    case_pack = loader.get_case_pack()
    if not case_pack:
        logger.error("No case pack loaded. Check data/HHGOA_IEEE/case_pack.csv")
        return

    if case_id:
        target = [c for c in case_pack if c.case_id == case_id]
        if not target:
            logger.error("Case %s not found", case_id)
            return
        answers = agent.investigate_all(target, output_dir=str(CASES_DIR))
    else:
        answers = agent.investigate_all(case_pack, output_dir=str(CASES_DIR))

    return answers


def run_server(cases_dir: str = None):
    import uvicorn
    cases = cases_dir or str(CASES_DIR)
    app = create_app(cases_dir=cases)
    logger.info("Dashboard on http://0.0.0.0:%d", PORT)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


def show_stats(graph: InMemoryGraph):
    stats = graph.get_graph_stats()
    print("\n=== Graph Statistics ===")
    for key, val in sorted(stats.items()):
        print(f"  {key}: {val:,}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Fraud Investigation Agent")
    subparsers = parser.add_subparsers(dest="command")

    inv = subparsers.add_parser("investigate", help="Run investigation")
    inv.add_argument("case_id", nargs="?", help="Specific case (e.g. HHG-001)")
    inv.add_argument("--max-txns", type=int, default=0)
    inv.add_argument("--legacy", action="store_true", help="use the original src/agent orchestrator")
    for name, hlp in (("build", "build data/store"), ("train", "train models"), ("validate", "validate cases/"),
                      ("backtest", "closed-case replay"), ("monitor", "autonomous monitoring sweep")):
        subparsers.add_parser(name, help=hlp)

    srv = subparsers.add_parser("serve", help="Start dashboard")
    srv.add_argument("--port", type=int, default=PORT)

    ld = subparsers.add_parser("load", help="Load data")
    ld.add_argument("--max-txns", type=int, default=0)

    subparsers.add_parser("stats", help="Show stats")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    ensure_dirs()
    import subprocess
    root = Path(__file__).parent
    if args.command == "investigate" and not args.legacy:
        from src.engine.runner import run
        run([args.case_id] if args.case_id else None)
        return
    if args.command == "build":
        from src.engine.store import build_store
        print(build_store(force=True))
        return
    if args.command in ("train", "validate", "backtest", "monitor"):
        script = {"train": "train_models.py", "validate": "validate_answers.py", "backtest": "backtest.py",
                  "monitor": "monitor.py"}[args.command]
        sys.exit(subprocess.call([sys.executable, str(root / "scripts" / script)]))
    if args.command == "serve":
        run_server()
        return

    graph = InMemoryGraph()
    if args.command == "investigate":
        loader = load_data(graph, getattr(args, "max_txns", 0))
        run_investigation(graph, loader, args.case_id)

    elif args.command == "load":
        loader = load_data(graph, getattr(args, "max_txns", 0))
        show_stats(graph)

    elif args.command == "stats":
        loader = load_data(graph)
        show_stats(graph)


if __name__ == "__main__":
    main()
