"""Trolley's tools over the Model Context Protocol, with the same guardrails in the tools themselves.

python -m trolley.mcp_server            # stdio, for desktop MCP clients
"""

from __future__ import annotations

import argparse
import inspect
import json

from mcp.server.mcpserver import MCPServer

from .agent.agent import Session
from .tools import TOOLS, Tool, ToolError

SESSION = Session()


def _handler(tool: Tool):
    fields = tool.args_model.model_fields
    params = [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            default=inspect.Parameter.empty if f.is_required() else f.get_default(call_default_factory=True),
            annotation=f.annotation,
        )
        for name, f in fields.items()
    ]

    def handler(**kwargs) -> str:
        try:
            return json.dumps(tool.run(SESSION.ctx, kwargs), default=str)
        except ToolError as e:
            raise ValueError(str(e)) from e

    handler.__name__ = tool.name
    handler.__doc__ = tool.description
    handler.__signature__ = inspect.Signature(params, return_annotation=str)
    handler.__annotations__ = {**{n: f.annotation for n, f in fields.items()}, "return": str}
    return handler


def build_server() -> MCPServer:
    server = MCPServer(
        name="trolley",
        instructions="Supermarket tools for one signed-in demo customer: search real products with allergy filters, "
        "manage the basket and orders, and report delivery problems. Actions that change an order return "
        "needs_confirmation; call confirm_action only after the customer agrees in a later message.",
    )
    for tool in TOOLS:
        server.add_tool(_handler(tool), name=tool.name, description=tool.description)
    return server


def main() -> None:
    ap = argparse.ArgumentParser(description="Trolley MCP server")
    ap.add_argument("--transport", default="stdio", choices=["stdio", "sse", "streamable-http"])
    build_server().run(transport=ap.parse_args().transport)


if __name__ == "__main__":
    main()
