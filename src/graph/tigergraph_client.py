"""
TigerGraph Client: real connection to TigerGraph Savanna or Community Edition.
Falls back to in-memory graph when TigerGraph is not available.
"""
from __future__ import annotations
import json
import logging
import re
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


class TigerGraphClient:
    """
    Connects to TigerGraph Savanna or Community Edition.
    Provides high-level query methods for the fraud investigation agent.
    """

    def __init__(self, host: str = "", token: str = "", graph: str = "FraudInvestigation"):
        self.host = host.rstrip("/")
        self.token = token
        self.graph = graph
        self._connected = False
        self._use_rest = True

    def connect(self) -> bool:
        """Establish connection to TigerGraph."""
        if not self.host:
            logger.warning("No TigerGraph host configured")
            return False

        import pyTigerGraph as tg
        user = os.getenv("TIGERGRAPH_USER") or os.getenv("TG_USERNAME", "tigergraph")
        password = os.getenv("TIGERGRAPH_SECRET") or os.getenv("TG_PASSWORD", "tigergraph")
        attempts = []
        cloud = "tgcloud.io" in self.host or os.getenv("TIGERGRAPH_USE_SAVANNA", "").lower() == "true"
        if self.token:  # Savanna API token
            attempts.append(("api token", dict(apiToken=self.token)))
        if os.getenv("TIGERGRAPH_SECRET"):  # a GSQL secret is exchanged for a REST token
            attempts.append(("gsql secret", dict(gsqlSecret=os.environ["TIGERGRAPH_SECRET"], username=user,
                                                 password=password)))
        if self.token:  # some Savanna "API tokens" are really secrets: exchange them for a REST token
            attempts.append(("api token as secret", dict(gsqlSecret=self.token, username=user, password=password)))
        attempts.append(("user/password", dict(username=user, password=password)))
        for label, kw in attempts:
            try:
                self.conn = tg.TigerGraphConnection(host=self.host, graphname=self.graph, tgCloud=cloud, **kw)
                if label in ("gsql secret", "api token as secret"):
                    self.conn.getToken(kw["gsqlSecret"])
                # /echo accepts any token, so verify with an authenticated RESTPP call.
                self.conn.getVertexCount("*")
                self._connected = True
                logger.info("Connected to TigerGraph at %s (%s)", self.host, label)
                return True
            except Exception as e:  # noqa: BLE001
                logger.warning("TigerGraph connection via %s failed: %s", label, str(e)[:200])
        return False

    def is_connected(self) -> bool:
        return self._connected

    def run_query(self, query_name: str, params: dict = None) -> Any:
        """Run a named GSQL query."""
        if not self._connected:
            raise RuntimeError("Not connected to TigerGraph")
        try:
            result = self.conn.runInstalledQuery(query_name, params or {})
            return result
        except Exception as e:
            logger.error("Query %s failed: %s", query_name, e)
            raise

    def upsert_vertex(self, vertex_type: str, vertex_id: str, attributes: dict):
        """Upsert a vertex into the graph."""
        if not self._connected:
            raise RuntimeError("Not connected to TigerGraph")
        self.conn.upsertVertex(vertex_type, vertex_id, attributes)

    def upsert_edge(self, source_type: str, source_id: str, edge_type: str,
                    target_type: str, target_id: str, attributes: dict = None):
        """Upsert an edge into the graph."""
        if not self._connected:
            raise RuntimeError("Not connected to TigerGraph")
        self.conn.upsertEdge(source_type, source_id, edge_type,
                             target_type, target_id, attributes or {})

    def get_vertex(self, vertex_type: str, vertex_id: str) -> Optional[dict]:
        """Get a vertex by ID."""
        if not self._connected:
            raise RuntimeError("Not connected to TigerGraph")
        try:
            result = self.conn.getVerticesById(vertex_type, vertex_id)
            if result and len(result) > 0:
                return result[0]
            return None
        except Exception:
            return None

    def get_neighbors(self, vertex_type: str, vertex_id: str,
                      edge_type: str, target_type: str,
                      limit: int = 100) -> list[dict]:
        """Get neighboring vertices."""
        if not self._connected:
            raise RuntimeError("Not connected to TigerGraph")
        try:
            result = self.conn.getNeighbors(vertex_type, vertex_id,
                                            edge_type, target_type, limit=limit)
            if result and "results" in result:
                return result["results"]
            return []
        except Exception as e:
            logger.error("getNeighbors failed: %s", e)
            return []

    # --- High-level fraud investigation queries ---

    def get_card_transaction_window(self, card_id: str,
                                     hours: int = 2) -> list[dict]:
        """Get transactions for a card within a time window."""
        query = """
        INTERPRET QUERY (?card_id, ?hours) FOR GRAPH FraudInvestigation {
            Start = {Card.*};
            Start = Start WHERE id == card_id;

            Txns = SELECT t FROM Start:s - (MADE:e) - Transaction:t
                    WHERE datetime_diff(now(), t.ts) < ?hours * 3600
                    ORDER BY t.ts
                    LIMIT 100;

            PRINT Txns;
        }
        """
        # Fallback to REST API if interpret not available
        return self._run_interpret_or_rest(query, {"card_id": card_id, "hours": hours})

    def get_device_neighbors(self, device_id: str) -> dict:
        """Get all cards and transactions linked to a device."""
        query = """
        INTERPRET QUERY (?device_id) FOR GRAPH FraudInvestigation {
            Start = {DeviceProfile.*};
            Start = Start WHERE id == device_id;

            Txns = SELECT t FROM Start:s - (<FROM_DEVICE:e) - Transaction:t
                    ORDER BY t.ts
                    LIMIT 200;

            Cards = SELECT c FROM Txns:t - (MADE:e) - Card:c
                    LIMIT 50;

            PRINT Txns, Cards;
        }
        """
        return self._run_interpret_or_rest(query, {"device_id": device_id})

    def find_shared_devices(self, card_ids: list[str]) -> list[dict]:
        """Find device profiles shared across multiple cards."""
        query = """
        INTERPRET QUERY (?card_ids) FOR GRAPH FraudInvestigation {
            Start = {Card.*};
            Start = Start WHERE id IN card_ids;

            Txns = SELECT t FROM Start:s - (MADE:e) - Transaction:t;

            Devices = SELECT d FROM Txns:t - (FROM_DEVICE:e) - DeviceProfile:d
                    ACCUM d.@card_count += 1
                    HAVING d.@card_count > 1
                    LIMIT 20;

            PRINT Devices;
        }
        """
        return self._run_interpret_or_rest(query, {"card_ids": card_ids})

    def find_similar_closed_cases(self, customer_id: str, card_id: str,
                                   pattern: str = "") -> list[dict]:
        """Find closed cases similar to current investigation."""
        query = """
        INTERPRET QUERY (?customer_id, ?card_id) FOR GRAPH FraudInvestigation {
            Cases = {ClosedCase.*};

            ByCustomer = SELECT c FROM Cases:c
                        WHERE c.customer_id == customer_id
                        LIMIT 10;

            ByCard = SELECT c FROM Cases:c
                    WHERE c.card_id == card_id
                    LIMIT 10;

            PRINT ByCustomer, ByCard;
        }
        """
        return self._run_interpret_or_rest(query, {
            "customer_id": customer_id,
            "card_id": card_id,
        })

    def detect_card_testing_gsql(self, card_id: str) -> dict:
        """Detect card testing pattern using GSQL."""
        query = """
        INTERPRET QUERY (?card_id) FOR GRAPH FraudInvestigation {
            Start = {Card.*};
            Start = Start WHERE id == card_id;

            AllTxns = SELECT t FROM Start:s - (MADE:e) - Transaction:t
                    ORDER BY t.ts
                    LIMIT 50;

            SmallOnline = SELECT t FROM AllTxns:t
                        WHERE t.channel == "online" AND t.amount < 5.0;

            LargeTxn = SELECT t FROM AllTxns:t
                      WHERE t.amount >= 50.0
                      LIMIT 5;

            PRINT SmallOnline, LargeTxn;
        }
        """
        return self._run_interpret_or_rest(query, {"card_id": card_id})

    def write_investigation_case(self, case_id: str, properties: dict,
                                  linked_txns: list[str] = None,
                                  linked_cards: list[str] = None,
                                  linked_devices: list[str] = None,
                                  linked_cases: list[str] = None):
        """Write a completed investigation case to the graph."""
        self.upsert_vertex("InvestigationCase", case_id, properties)

        if linked_txns:
            for txn_id in linked_txns:
                self.upsert_edge("InvestigationCase", case_id,
                                "CASE_INVOLVES_TXN", "Transaction", txn_id)

        if linked_cards:
            for card_id in linked_cards:
                self.upsert_edge("InvestigationCase", case_id,
                                "CASE_ON_CARD", "Card", card_id)

        if linked_devices:
            for device_id in linked_devices:
                self.upsert_edge("InvestigationCase", case_id,
                                "CASE_INVOLVES_DEVICE", "DeviceProfile", device_id)

        if linked_cases:
            for closed_id in linked_cases:
                self.upsert_edge("InvestigationCase", case_id,
                                "CASE_SIMILAR_TO", "ClosedCase", closed_id,
                                {"similarity_score": 0.8})

        logger.info("Investigation case %s written to TigerGraph", case_id)

    def _schema(self) -> tuple[dict, set]:
        """Live vertex attributes per type and edge type names (the deployed schema can differ
        from tigergraph/schema.gsql, e.g. an older InvestigationCase definition)."""
        if getattr(self, "_schema_cache", None) is None:
            vt = {}
            for t in self.conn.getVertexTypes():
                try:
                    vt[t] = {a["AttributeName"] for a in self.conn.getVertexType(t).get("Attributes", [])}
                except Exception:  # noqa: BLE001
                    vt[t] = set()
            self._schema_cache = (vt, set(self.conn.getEdgeTypes()))
        return self._schema_cache

    def upsert_agent_case(self, rec: dict) -> bool:
        """Write an engine case (src/engine) as an InvestigationCase vertex with edges.

        Only attributes/edges that exist in the deployed schema are sent. Returns True only
        when the vertex can be read back; written_to_graph is taken from this value."""
        if not self._connected and not self.connect():
            return False
        try:
            vtypes, etypes = self._schema()
            if "InvestigationCase" not in vtypes:
                logger.warning("upsert_agent_case: no InvestigationCase vertex type in the live schema")
                return False
            status = ("closed_fraud" if rec["verdict"] == "fraud" else
                      "closed_legitimate" if rec["verdict"] == "legitimate" else "open")
            props = {"customer_id": rec["customer_id"], "card_id": (rec["cards"] or [""])[0],
                     "opened_at": rec["opened_at"], "verdict": rec["verdict"], "pattern": rec["pattern"],
                     "status": status, "fraud_probability": rec.get("p"), "exposure_usd": rec.get("exposure")}
            allowed = vtypes["InvestigationCase"]
            props = {k: v for k, v in props.items() if v is not None and (not allowed or k in allowed)}
            cid = rec["graph_case_id"]
            for _ in range(len(props) + 1):
                try:
                    self.upsert_vertex("InvestigationCase", cid, props)
                    break
                except Exception as e:  # noqa: BLE001
                    m = re.search(r"Unknown vertex attribute or vector name: (\w+)", str(e))
                    if not m or m.group(1) not in props:
                        raise
                    logger.info("InvestigationCase has no attribute %s in the live schema; dropping it", m.group(1))
                    props.pop(m.group(1))
            links = [("CASE_INVOLVES_TXN", "Transaction", rec.get("txn_ids") or []),
                     ("CASE_ON_CARD", "Card", rec.get("cards") or []),
                     ("CASE_INVOLVES_DEVICE", "DeviceProfile", rec.get("devices") or [])]
            n_ok = n_fail = 0
            for et, tt, ids in links:
                if et not in etypes:
                    continue
                for tid in ids:
                    try:
                        self.upsert_edge("InvestigationCase", cid, et, tt, str(tid))
                        n_ok += 1
                    except Exception as e:  # noqa: BLE001
                        n_fail += 1
                        logger.debug("edge %s -> %s failed: %s", et, tid, e)
            ok = self.get_vertex("InvestigationCase", cid) is not None
            logger.info("TigerGraph: %s written=%s (%d attrs, %d edges, %d edge failures)",
                        cid, ok, len(props), n_ok, n_fail)
            return ok
        except Exception as e:  # noqa: BLE001
            logger.warning("upsert_agent_case failed: %s", e)
            return False

    def _run_interpret_or_rest(self, query: str, params: dict) -> Any:
        """Run an interpret query, falling back to REST if needed."""
        if not self._connected:
            return {"error": "Not connected"}
        try:
            return self.conn.runInterpretedQuery(query, params)
        except Exception as e:
            logger.warning("Interpret query failed: %s", e)
            return {"error": str(e)}

    def get_graph_stats(self) -> dict:
        """Get vertex and edge counts."""
        if not self._connected:
            return {}
        try:
            stats = {}
            for vtype in ["Customer", "Card", "Transaction", "DeviceProfile",
                          "EmailDomain", "BillingRegion", "ClosedCase",
                          "InvestigationCase"]:
                count = self.conn.getVertexCount(vtype)
                stats[f"vertices_{vtype}"] = count
            return stats
        except Exception as e:
            logger.error("getGraphStats failed: %s", e)
            return {}


def create_tigergraph_client() -> TigerGraphClient:
    """Create and connect a TigerGraph client from environment config."""
    host = os.getenv("TIGERGRAPH_HOST", "")
    token = os.getenv("TIGERGRAPH_TOKEN", "") or os.getenv("TIGERGRAPH_API_TOKEN", "")
    graph = os.getenv("TIGERGRAPH_GRAPH", "FraudInvestigation")

    client = TigerGraphClient(host=host, token=token, graph=graph)
    if host:
        client.connect()
    return client
