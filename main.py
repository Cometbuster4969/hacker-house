#!/usr/bin/env python3
"""
Fraud Investigation Agent — Main Entry Point

Usage:
    python main.py investigate          Run all 20 benchmark cases
    python main.py investigate HHG-001  Run a single case
    python main.py serve                Start the web dashboard
    python main.py load                 Load data into the graph
    python main.py stats                Show graph statistics
"""
import argparse
import json
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.utils.config import DATA_DIR, CASES_DIR, PORT, ensure_dirs
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
    """Load dataset into the graph."""
    loader = DataLoader(str(DATA_DIR), graph)
    loader.load_all(max_transactions=max_transactions)
    return loader


def run_investigation(graph: InMemoryGraph, loader: DataLoader,
                      case_id: str = None):
    """Run investigation on one or all cases."""
    agent = FraudInvestigationAgent(graph)

    case_pack = loader.get_case_pack()
    if not case_pack:
        logger.error("No case pack loaded. Check data/HHGOA_IEEE/case_pack.csv")
        return

    if case_id:
        # Run single case
        target = [c for c in case_pack if c.case_id == case_id]
        if not target:
            logger.error("Case %s not found in case pack", case_id)
            return
        answers = agent.investigate_all(target, output_dir=str(CASES_DIR))
    else:
        # Run all cases
        answers = agent.investigate_all(case_pack, output_dir=str(CASES_DIR))

    return answers


def run_server(cases_dir: str = None):
    """Start the web dashboard."""
    import uvicorn

    cases = cases_dir or str(CASES_DIR)
    app = create_app(cases_dir=cases)
    logger.info("Starting dashboard on http://0.0.0.0:%d", PORT)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


def show_stats(graph: InMemoryGraph):
    """Display graph statistics."""
    stats = graph.get_graph_stats()
    print("\n=== Graph Statistics ===")
    for key, val in sorted(stats.items()):
        print(f"  {key}: {val:,}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Fraud Investigation Agent — TigerGraph + AI"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # investigate
    inv_parser = subparsers.add_parser("investigate", help="Run fraud investigation")
    inv_parser.add_argument("case_id", nargs="?", help="Specific case ID (e.g. HHG-001)")
    inv_parser.add_argument("--max-txns", type=int, default=0,
                            help="Max transactions to load (0=all)")

    # serve
    serve_parser = subparsers.add_parser("serve", help="Start web dashboard")
    serve_parser.add_argument("--port", type=int, default=PORT)

    # load
    load_parser = subparsers.add_parser("load", help="Load data into graph")
    load_parser.add_argument("--max-txns", type=int, default=0,
                             help="Max transactions to load (0=all)")

    # stats
    subparsers.add_parser("stats", help="Show graph statistics")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    ensure_dirs()
    graph = InMemoryGraph()

    if args.command == "investigate":
        logger.info("Loading data...")
        loader = load_data(graph, getattr(args, "max_txns", 0))
        logger.info("Starting investigation...")
        run_investigation(graph, loader, args.case_id)

    elif args.command == "serve":
        logger.info("Loading data for dashboard...")
        loader = load_data(graph)
        # Set graph stats for the dashboard
        from src.ui import app as ui_app
        ui_app._graph_stats = graph.get_graph_stats()
        run_server()

    elif args.command == "load":
        logger.info("Loading data...")
        loader = load_data(graph, getattr(args, "max_txns", 0))
        show_stats(graph)

    elif args.command == "stats":
        loader = load_data(graph)
        show_stats(graph)


if __name__ == "__main__":
    main()
