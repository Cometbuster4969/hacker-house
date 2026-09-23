"""
TigerGraph MCP Server: exposes graph operations as tools for the agent.
Implements the Model Context Protocol for TigerGraph integration.
"""
from __future__ import annotations
import json
import logging
from typing import Any

from ..graph.in_memory_graph import InMemoryGraph

logger = logging.getLogger(__name__)


class TigerGraphMCPServer:
    """
    MCP-compatible server exposing TigerGraph graph operations as tools.
    Can be used standalone or integrated with agent frameworks.
    """

    def __init__(self, graph: InMemoryGraph):
        self.graph = graph
        self._tools = self._define_tools()

    def _define_tools(self) -> dict[str, dict]:
        """Define the available MCP tools."""
        return {
            "get_transaction": {
                "name": "get_transaction",
                "description": "Retrieve a transaction by ID from the graph",
                "parameters": {
                    "transaction_id": {"type": "string", "description": "Transaction ID"}
                },
            },
            "get_card_transactions": {
                "name": "get_card_transactions",
                "description": "Get recent transactions for a card, ordered by time",
                "parameters": {
                    "card_id": {"type": "string", "description": "Card ID"},
                    "limit": {"type": "integer", "description": "Max transactions", "default": 30},
                },
            },
            "get_customer_cards": {
                "name": "get_customer_cards",
                "description": "Get all cards belonging to a customer",
                "parameters": {
                    "customer_id": {"type": "string", "description": "Customer ID"}
                },
            },
            "get_customer_transactions": {
                "name": "get_customer_transactions",
                "description": "Get recent transactions across all customer's cards",
                "parameters": {
                    "customer_id": {"type": "string", "description": "Customer ID"},
                    "days": {"type": "integer", "description": "Lookback days", "default": 90},
                },
            },
            "get_device_profile": {
                "name": "get_device_profile",
                "description": "Get device profile details",
                "parameters": {
                    "device_id": {"type": "string", "description": "Device profile ID"}
                },
            },
            "get_device_cards": {
                "name": "get_device_cards",
                "description": "Get all cards that used a specific device profile",
                "parameters": {
                    "device_id": {"type": "string", "description": "Device profile ID"}
                },
            },
            "get_billing_region_cards": {
                "name": "get_billing_region_cards",
                "description": "Get all cards transacting in a billing region",
                "parameters": {
                    "region_id": {"type": "string", "description": "Billing region ID"}
                },
            },
            "find_shared_devices": {
                "name": "find_shared_devices",
                "description": "Find device profiles shared across multiple cards (fraud ring detection)",
                "parameters": {
                    "card_ids": {"type": "array", "items": {"type": "string"},
                                 "description": "Card IDs to check"}
                },
            },
            "detect_card_testing": {
                "name": "detect_card_testing",
                "description": "Detect card testing pattern (small authorizations then larger purchase)",
                "parameters": {
                    "card_id": {"type": "string", "description": "Card ID"}
                },
            },
            "detect_out_of_region": {
                "name": "detect_out_of_region",
                "description": "Detect out-of-region usage pattern",
                "parameters": {
                    "card_id": {"type": "string", "description": "Card ID"},
                    "region": {"type": "number", "description": "Billing region to check"},
                },
            },
            "get_card_velocity": {
                "name": "get_card_velocity",
                "description": "Get transaction velocity metrics for a card",
                "parameters": {
                    "card_id": {"type": "string", "description": "Card ID"},
                    "hours": {"type": "integer", "description": "Window in hours", "default": 24},
                },
            },
            "find_similar_closed_cases": {
                "name": "find_similar_closed_cases",
                "description": "Find similar closed cases from case memory",
                "parameters": {
                    "customer_id": {"type": "string", "description": "Customer ID"},
                    "card_id": {"type": "string", "description": "Card ID"},
                },
            },
            "get_closed_cases": {
                "name": "get_closed_cases",
                "description": "Get closed cases for a customer or card",
                "parameters": {
                    "customer_id": {"type": "string", "description": "Customer ID", "default": ""},
                    "card_id": {"type": "string", "description": "Card ID", "default": ""},
                },
            },
            "write_investigation_case": {
                "name": "write_investigation_case",
                "description": "Write an investigation case to the graph (case memory)",
                "parameters": {
                    "case_id": {"type": "string", "description": "Case ID"},
                    "properties": {"type": "object", "description": "Case properties"},
                },
            },
            "get_graph_stats": {
                "name": "get_graph_stats",
                "description": "Get graph database statistics (vertex and edge counts)",
                "parameters": {},
            },
        }

    def list_tools(self) -> list[dict]:
        """List all available tools."""
        return list(self._tools.values())

    def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Execute a tool by name with given arguments."""
        if tool_name not in self._tools:
            return {"error": f"Unknown tool: {tool_name}"}

        logger.info("MCP tool call: %s(%s)", tool_name, json.dumps(arguments))

        try:
            if tool_name == "get_transaction":
                return self.graph.get_vertex("Transaction", arguments["transaction_id"])
            elif tool_name == "get_card_transactions":
                return self.graph.get_card_transactions(
                    arguments["card_id"],
                    arguments.get("limit", 30)
                )
            elif tool_name == "get_customer_cards":
                return self.graph.get_customer_cards(arguments["customer_id"])
            elif tool_name == "get_customer_transactions":
                return self.graph.get_customer_transactions(
                    arguments["customer_id"],
                    arguments.get("days", 90)
                )
            elif tool_name == "get_device_profile":
                return self.graph.get_vertex("DeviceProfile", arguments["device_id"])
            elif tool_name == "get_device_cards":
                return self.graph.get_device_cards(arguments["device_id"])
            elif tool_name == "get_billing_region_cards":
                return self.graph.get_region_cards(arguments["region_id"])
            elif tool_name == "find_shared_devices":
                return self.graph.find_shared_devices(arguments["card_ids"])
            elif tool_name == "detect_card_testing":
                return self.graph.detect_card_testing(arguments["card_id"])
            elif tool_name == "detect_out_of_region":
                return self.graph.detect_out_of_region(
                    arguments["card_id"], arguments["region"]
                )
            elif tool_name == "get_card_velocity":
                return self.graph.get_card_velocity(
                    arguments["card_id"], arguments.get("hours", 24)
                )
            elif tool_name == "find_similar_closed_cases":
                return self.graph.find_similar_cases(
                    arguments["customer_id"], arguments["card_id"]
                )
            elif tool_name == "get_closed_cases":
                if arguments.get("card_id"):
                    return self.graph.get_closed_cases_for_card(arguments["card_id"])
                return self.graph.get_closed_cases_for_customer(
                    arguments.get("customer_id", "")
                )
            elif tool_name == "write_investigation_case":
                self.graph.write_investigation_case(
                    arguments["case_id"], arguments["properties"]
                )
                return {"status": "written", "case_id": arguments["case_id"]}
            elif tool_name == "get_graph_stats":
                return self.graph.get_graph_stats()
            else:
                return {"error": f"Tool not implemented: {tool_name}"}

        except Exception as e:
            logger.error("MCP tool error: %s - %s", tool_name, e)
            return {"error": str(e)}

    def get_tools_for_llm(self) -> list[dict]:
        """Return tools in OpenAI function-calling format."""
        llm_tools = []
        for tool in self._tools.values():
            llm_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": {
                        "type": "object",
                        "properties": tool["parameters"],
                        "required": [
                            k for k, v in tool["parameters"].items()
                            if "default" not in v
                        ],
                    },
                },
            })
        return llm_tools
