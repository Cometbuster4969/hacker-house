#!/usr/bin/env python3
"""
Deploy the TigerGraph schema.
Run this after setting up TigerGraph Savanna or CE.

Usage:
    python scripts/deploy_schema.py

Requires TIGERGRAPH_HOST and TIGERGRAPH_TOKEN in .env
"""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()


def main():
    host = os.getenv("TIGERGRAPH_HOST", "")
    token = os.getenv("TIGERGRAPH_TOKEN", "")
    graph = os.getenv("TIGERGRAPH_GRAPH", "FraudInvestigation")

    if not host:
        print("ERROR: Set TIGERGRAPH_HOST in .env")
        print("Example: TIGERGRAPH_HOST=https://your-workspace.tgcloud.io")
        sys.exit(1)

    print(f"Deploying schema to {host}...")

    try:
        import pyTigerGraph as tg

        if token:
            conn = tg.TigerGraphConnection(host=host, graphname=graph, apiToken=token)
        else:
            conn = tg.TigerGraphConnection(
                host=host, graphname=graph,
                username=os.getenv("TG_USERNAME", "tigergraph"),
                password=os.getenv("TG_PASSWORD", "tigergraph"),
            )

        # Read schema file
        schema_path = Path(__file__).parent.parent / "tigergraph" / "schema.gsql"
        if not schema_path.exists():
            print(f"ERROR: Schema file not found at {schema_path}")
            sys.exit(1)

        schema_sql = schema_path.read_text()

        # Execute schema
        print("Executing schema...")
        result = conn.gsql(schema_sql)
        print(result)

        print("\nSchema deployed successfully!")
        print(f"Graph: {graph}")

    except ImportError:
        print("ERROR: pyTigerGraph not installed. Run: pip install pyTigerGraph")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        print("\nTroubleshooting:")
        print("1. Check TIGERGRAPH_HOST is correct")
        print("2. Check TIGERGRAPH_TOKEN is valid")
        print("3. Make sure the workspace is running (not auto-stopped)")
        sys.exit(1)


if __name__ == "__main__":
    main()
