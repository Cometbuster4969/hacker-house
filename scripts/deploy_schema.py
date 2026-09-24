#!/usr/bin/env python3
"""
Deploy the TigerGraph schema in two steps:
  1. Create vertices and edges (schema.gsql)
  2. Create the graph (install_schema.gsql)
GSQL quirks this handles:
  - No inline comments (//) — parser rejects them
  - No DEFAULT "" — use DEFAULT " " or omit
  - DDL and CREATE GRAPH may need separate batches
  - primary_id_as_attribute="true" must be quoted
Usage:
    python scripts/deploy_schema.py
Requires TIGERGRAPH_HOST and TIGERGRAPH_TOKEN in .env
"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
# Load .env manually (don't require dotenv)
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
def main():
    host = os.getenv("TIGERGRAPH_HOST", "")
    token = os.getenv("TIGERGRAPH_TOKEN", "")
    graph = os.getenv("TIGERGRAPH_GRAPH", "FraudInvestigation")
    if not host:
        print("ERROR: Set TIGERGRAPH_HOST in .env")
        print("Example: TIGERGRAPH_HOST=https://your-workspace.tgcloud.io")
        sys.exit(1)
    schema_dir = Path(__file__).parent.parent / "tigergraph"
    schema_file = schema_dir / "schema.gsql"
    install_file = schema_dir / "install_schema.gsql"
    if not schema_file.exists():
        print(f"ERROR: {schema_file} not found")
        sys.exit(1)
    print(f"Connecting to {host}...")
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
        # Verify connection
        conn.echo()
        print("Connected.\n")
        # Step 1: Create vertices and edges
        print("=" * 60)
        print("STEP 1: Creating vertices and edges...")
        print("=" * 60)
        schema_sql = schema_file.read_text()
        # Remove the CREATE GRAPH statement from schema.gsql if present
        # (it goes in install_schema.gsql)
        lines = schema_sql.split("\n")
        filtered = []
        skip = False
        for line in lines:
            if line.strip().startswith("CREATE GRAPH"):
                skip = True
            if not skip:
                filtered.append(line)
        schema_sql = "\n".join(filtered)
        result = conn.gsql(schema_sql)
        print(result)
        if "Error" in result or "error" in result.lower():
            print("\n⚠️  Schema creation had errors. Check output above.")
            print("Common fixes:")
            print("  - 'Encountered \"\"' → check for // comments or DEFAULT \"\"")
            print("  - 'already exists' → schema is already deployed, skip to step 2")
        else:
            print("\n✅ Vertices and edges created.\n")
        # Step 2: Create the graph
        print("=" * 60)
        print("STEP 2: Creating graph...")
        print("=" * 60)
        if install_file.exists():
            install_sql = install_file.read_text()
        else:
            install_sql = f"CREATE GRAPH {graph} (\n"
            install_sql += "    Customer, Card, Transaction, DeviceProfile,\n"
            install_sql += "    EmailDomain, BillingRegion, ClosedCase, InvestigationCase,\n"
            install_sql += "    OWNS, MADE, FROM_DEVICE, PURCHASER_EMAIL, RECIPIENT_EMAIL,\n"
            install_sql += "    BILLED_IN, NEXT_TXN, INVOLVES_TXN, ON_CARD, CONNECTED_CARD,\n"
            install_sql += "    SHARES_DEVICE, SAME_REGION, CASE_INVOLVES_TXN, CASE_ON_CARD,\n"
            install_sql += "    CASE_CONNECTED_CARD, CASE_INVOLVES_DEVICE, CASE_SIMILAR_TO,\n"
            install_sql += "    LINKED_CUSTOMER\n"
            install_sql += ");\n"
        result = conn.gsql(install_sql)
        print(result)
        if "Error" in result or "error" in result.lower():
            print("\n⚠️  Graph creation had errors.")
            if "already exists" in result.lower():
                print("Graph already exists — this is fine.")
        else:
            print("\n✅ Graph created.\n")
        # Step 3: Verify
        print("=" * 60)
        print("STEP 3: Verifying schema...")
        print("=" * 60)
        try:
            schema = conn.getSchema()
            print(schema)
            print("\n✅ Schema verified.")
        except Exception as e:
            print(f"Could not auto-verify: {e}")
            print("Run manually: gsql 'USE GRAPH FraudInvestigation; ls'")
        print("\n" + "=" * 60)
        print("DEPLOYMENT COMPLETE")
        print("=" * 60)
        print(f"Graph: {graph}")
        print(f"Host: {host}")
        print(f"\nNext step: python scripts/load_to_tigergraph.py")
    except ImportError:
        print("ERROR: pyTigerGraph not installed.")
        print("Run: pip install pyTigerGraph")
        print("\nAlternative: deploy manually via GSQL shell:")
        print(f"  1. gsql {schema_file}")
        print(f"  2. gsql {install_file}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        print("\nTroubleshooting:")
        print("1. Check TIGERGRAPH_HOST is correct (include https://)")
        print("2. Check TIGERGRAPH_TOKEN is valid")
        print("3. Make sure workspace is running (not auto-stopped)")
        print("4. Try manual deployment:")
        print(f"   gsql {schema_file}")
        print(f"   gsql {install_file}")
        sys.exit(1)
if __name__ == "__main__":
    main()

