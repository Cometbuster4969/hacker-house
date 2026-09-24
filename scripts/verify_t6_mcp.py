#!/usr/bin/env python3
"""T6 (+T5 stretch) verification — official tigergraph-mcp against Savanna.

Stage 1 — MCP smoke: connections / schema / vertex count / installed query / GSQL,
          with tool-name resolution that tolerates the "tigergraph__" prefix.
Stage 2 — TigerVector probe: add a VECTOR attribute on ClosedCase, upsert 200
          deterministic 128-dim vectors, run top-k similarity via MCP, and
          compare against a locally computed cosine ranking.

Evidence: cases/mcp_tool_verification.json
"""
import asyncio
import json
import math
import os
import random
import re
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

TG_HOST = os.getenv("TIGERGRAPH_HOST", "https://tg-0d7f3ac1-7c8e-4b37-b81d-9f15452c48e0.tg-2635877100.i.tgcloud.io")
TG_GRAPH = os.getenv("TIGERGRAPH_GRAPH", "FraudInvestigation")
TG_USER = os.getenv("TIGERGRAPH_USER", "ayush")
TG_SECRET = os.getenv("TIGERGRAPH_SECRET", "")

EVIDENCE_PATH = Path(__file__).parent.parent / "cases" / "mcp_tool_verification.json"

VEC_ATTR = "case_embedding"
VEC_DIM = 128
N_VEC = 200
TOP_K = 5

CANDIDATES = {
    "connections": ["list_connections", "get_connections", "show_connections"],
    "schema": ["get_graph_schema", "get_schema", "graph_schema", "show_schema"],
    "vertex_count": ["get_vertex_count", "vertex_count", "get_vertex_amount", "count_vertices"],
    "installed_query": ["run_installed_query", "run_installed_query_v2", "execute_installed_query", "run_query"],
    "gsql": ["gsql", "run_gsql", "execute_gsql"],
}

# semantic key -> substrings that identify the actual schema property name
KEYWORDS = {
    "graph": ["graph"],
    "vertex_type": ["vertex_type", "vtype", "node_type", "vertex"],
    "attribute": ["attribute", "attr", "field"],
    "dimension": ["dim"],
    "vectors": ["vector", "item", "record", "upsert", "data", "entries"],
    "query_vector": ["query", "search_vector", "vector"],
    "top_k": ["top_k", "limit", "num"],
    "query_name": ["query_name", "query"],
    "params": ["param", "arg"],
    "command": ["command", "gsql", "statement", "script"],
}


def _get(obj, *names, default=None):
    """Version-tolerant attribute read: MCP SDK 1.x uses camelCase, 2.x snake_case."""
    for n in names:
        try:
            v = getattr(obj, n)
        except AttributeError:
            continue
        if v is not None:
            return v
    return default


def find_server_cmd():
    exe = shutil.which("tigergraph-mcp")
    if exe:
        return [exe]
    scripts_dir = Path(sys.executable).parent
    for name in ("tigergraph-mcp.exe", "tigergraph-mcp"):
        p = scripts_dir / name
        if p.exists():
            return [str(p)]
    return None


def make_vectors():
    """Deterministic normalised vectors + the query vector."""
    rng = random.Random(42)
    vecs = {}
    for i in range(N_VEC):
        v = [rng.uniform(-1, 1) for _ in range(VEC_DIM)]
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        vecs[f"SYN-{i:03d}"] = [x / n for x in v]
    q = [rng.uniform(-1, 1) for _ in range(VEC_DIM)]
    n = math.sqrt(sum(x * x for x in q)) or 1.0
    return vecs, [x / n for x in q]


def local_top_k(vecs, query, k=TOP_K):
    scored = sorted(vecs.items(), key=lambda kv: -sum(a * b for a, b in zip(kv[1], query)))
    return [cid for cid, _ in scored[:k]]


def build_args(schema, wishes):
    """Map semantic wishes onto a tool inputSchema's real property names."""
    props = (schema or {}).get("properties", {}) or {}
    args, used, missing_required = {}, set(), []
    for sem, val in wishes.items():
        keys = KEYWORDS.get(sem, [sem])
        for prop in props:
            if prop in used:
                continue
            pl = prop.lower()
            if any(k in pl for k in keys):
                args[prop] = val
                used.add(prop)
                break
    for prop in (schema or {}).get("required", []) or []:
        if prop not in args:
            missing_required.append(prop)
    return args, missing_required


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    cmd = find_server_cmd()
    if not cmd:
        print("ERROR: tigergraph-mcp not found. Run:  pip install tigergraph-mcp")
        sys.exit(1)

    env = dict(os.environ)
    env.update({
        "TG_HOST": TG_HOST,
        "TG_GRAPHNAME": TG_GRAPH,
        "TG_USERNAME": TG_USER,
        "TG_PASSWORD": TG_SECRET,
    })
    print(f"Spawning: {cmd[0]}  (graph={TG_GRAPH})")

    params = StdioServerParameters(command=cmd[0], env=env)
    evidence = {
        "server_command": cmd[0],
        "graph": TG_GRAPH,
        "host": TG_HOST,
        "checks": {},
        "tigervector": {},
    }

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            s_info = _get(init, "server_info", "serverInfo", default=None)
            evidence["server_info"] = {
                "name": _get(s_info, "name", default=None) if s_info else None,
                "version": _get(s_info, "version", default=None) if s_info else None,
                "protocol": _get(init, "protocol_version", "protocolVersion", default=None),
            }
            print(f"Connected. server={evidence['server_info']}")

            tools_resp = await session.list_tools()
            tools = _get(tools_resp, "tools", default=[])
            tool_names = [t.name for t in tools]
            schemas = {t.name: (_get(t, "inputSchema", "input_schema", default=None) or {})
                       for t in tools}
            evidence["tool_count"] = len(tool_names)
            evidence["tools"] = tool_names
            print(f"Tools exposed ({len(tool_names)})")

            def resolve(bare):
                if bare in tool_names:
                    return bare
                if f"tigergraph__{bare}" in tool_names:
                    return f"tigergraph__{bare}"
                for tn in tool_names:
                    if tn.endswith(bare):
                        return tn
                return None

            async def call_resolved(bare, wishes):
                """Resolve a bare tool name, map wishes onto its schema, call it."""
                name = resolve(bare)
                if not name:
                    return None, False, "no matching tool", None
                args, missing = build_args(schemas.get(name), wishes)
                try:
                    resp = await session.call_tool(name, args)
                    content = _get(resp, "content", default=[]) or []
                    text = "".join(_get(c, "text", default="") for c in content)
                    ok = not bool(_get(resp, "isError", default=False))
                    return name, ok, text[:1500], {"args": args, "missing_required": missing}
                except Exception as e:
                    return name, False, f"EXC: {type(e).__name__}: {str(e)[:300]}", \
                           {"args": args, "missing_required": missing}

            # ---------- Stage 1: smoke ----------
            stage1 = [
                ("connections", "connections", {}),
                ("schema", "schema", {"graph": TG_GRAPH}),
                ("vertex_count", "vertex_count", {"graph": TG_GRAPH, "vertex_type": "Transaction"}),
                ("installed_query", "installed_query",
                 {"graph": TG_GRAPH, "query_name": "graph_stats", "params": {}}),
                ("gsql", "gsql", {"graph": TG_GRAPH, "command": "SHOW VERTEX Transaction"}),
            ]
            for key, bare, wishes in stage1:
                try:
                    name, ok, text, meta = await call_resolved(bare, wishes)
                except Exception as e:
                    name, ok, text, meta = None, False, \
                        f"CHECK-EXC: {type(e).__name__}: {str(e)[:300]}", None
                evidence["checks"][key] = {"tool": name, "ok": ok,
                                           "excerpt": text[:600], "call_meta": meta}
                print(f"[{'OK ' if ok else 'FAIL'}] {key} via {name}")

            # ---------- Stage 2: TigerVector probe ----------
            vecs, query = make_vectors()
            tv = evidence["tigervector"]

            name, ok, text, meta = await call_resolved(
                "add_vector_attribute",
                {"graph": TG_GRAPH, "vertex_type": "ClosedCase",
                 "attribute": VEC_ATTR, "dimension": VEC_DIM})
            tv["add_attribute"] = {"tool": name, "ok": ok, "excerpt": text[:500], "call_meta": meta}
            print(f"[{'OK ' if ok else 'FAIL'}] add_vector_attribute -> {ok}")
            if not ok:
                tv["supported"] = False
                tv["note"] = "TigerVector attribute creation rejected — see excerpt/schemas."
            else:
                tv["supported"] = True
                batch_size = 50
                items = list(vecs.items())
                upsert_ok = 0
                for b in range(0, len(items), batch_size):
                    batch = [{"id": cid, "values": v} for cid, v in items[b:b + batch_size]]
                    name, ok, text, meta = await call_resolved(
                        "upsert_vectors",
                        {"graph": TG_GRAPH, "vertex_type": "ClosedCase",
                         "attribute": VEC_ATTR, "vectors": batch})
                    upsert_ok += 1 if ok else 0
                    if not ok:
                        tv["upsert_error"] = text[:500]
                        tv["upsert_error_args"] = meta
                        break
                tv["upsert_batches_ok"] = upsert_ok
                print(f"upsert batches OK: {upsert_ok}")

                if upsert_ok == (len(items) + batch_size - 1) // batch_size:
                    name, ok, text, meta = await call_resolved(
                        "search_top_k_similarity",
                        {"graph": TG_GRAPH, "vertex_type": "ClosedCase",
                         "attribute": VEC_ATTR, "query_vector": query, "top_k": TOP_K})
                    tv["search"] = {"tool": name, "ok": ok, "excerpt": text[:800],
                                    "call_meta": meta}
                    tg_ids = re.findall(r"SYN-\d{3}", text)
                    expected = local_top_k(vecs, query)
                    tv["expected_local_top5"] = expected
                    tv["tg_top5_in_order"] = tg_ids[:TOP_K]
                    overlap = len(set(tg_ids[:TOP_K]) & set(expected))
                    tv["top5_overlap"] = overlap
                    print(f"search_top_k_similarity ok={ok}; overlap with local cosine: "
                          f"{overlap}/{TOP_K}")
                    print(f"  local : {expected}")
                    print(f"  tiger : {tg_ids[:TOP_K]}")

                    name, ok, text, meta = await call_resolved(
                        "fetch_vector",
                        {"graph": TG_GRAPH, "vertex_type": "ClosedCase",
                         "attribute": VEC_ATTR, "vertex_id": "SYN-000"})
                    tv["fetch"] = {"tool": name, "ok": ok, "excerpt": text[:300]}
                    print(f"[{'OK ' if ok else 'FAIL'}] fetch_vector -> {ok}")

            # stash vector-tool schemas so we can adapt without another roundtrip
            tv["tool_schemas"] = {n: schemas[n] for n in tool_names
                                  if "vector" in n or n.endswith(("upsert_vectors",
                                                                  "search_top_k_similarity",
                                                                  "fetch_vector"))}

    passed = sum(1 for c in evidence["checks"].values() if c.get("ok"))
    evidence["stage1_passed"] = passed
    evidence["stage1_total"] = len(evidence["checks"])
    EVIDENCE_PATH.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"\n=== MCP smoke: {passed}/{len(evidence['checks'])} checks passed ===")
    print(f"=== TigerVector: supported={evidence['tigervector'].get('supported')} ===")
    print(f"Evidence written to {EVIDENCE_PATH}")


if __name__ == "__main__":
    asyncio.run(main())