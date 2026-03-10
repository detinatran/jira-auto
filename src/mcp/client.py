"""
MCP Client Manager — connects to Google Sheets and Atlassian MCP servers.

Provides a unified interface for agents and pipelines to discover and
call tools from both services through the Model Context Protocol.

Both MCP servers run as subprocesses communicating via stdio transport:
  - mcp-google-sheets  → Google Sheets API tools
  - mcp-atlassian      → Jira + Confluence API tools
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

from src.utils import config

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
#  Server parameter helper
# ════════════════════════════════════════════════════════════════════════════

def _build_server_params(command: str, args: list[str] | None = None,
                         extra_env: dict[str, str] | None = None) -> StdioServerParameters:
    """Build a StdioServerParameters for stdio_client."""
    env = {k: v for k, v in os.environ.items()}
    if extra_env:
        env.update(extra_env)
    return StdioServerParameters(
        command=command,
        args=args or [],
        env=env,
    )


# ════════════════════════════════════════════════════════════════════════════
#  MCP Client Manager
# ════════════════════════════════════════════════════════════════════════════

class MCPClientManager:
    """Manages connections to Google Sheets MCP + Atlassian MCP servers.

    Usage::

        async with MCPClientManager().connect() as mcp:
            tools = mcp.list_tool_names()
            result = await mcp.call_tool("sheet_get_sheet_data", {...})
    """

    def __init__(self):
        self.sheet_session: Optional[ClientSession] = None
        self.jira_session: Optional[ClientSession] = None
        self._sheet_tools: list = []
        self._jira_tools: list = []
        # tool_name → (session, original_mcp_tool_name, schema_dict)
        self._tool_registry: dict[str, tuple[ClientSession, str, dict]] = {}

    # ── Connection ──────────────────────────────────────────────────────

    @asynccontextmanager
    async def connect(self):
        """Async context-manager: start both MCP servers and connect."""
        sheet_env = {
            "SERVICE_ACCOUNT_PATH": config.GOOGLE_SERVICE_ACCOUNT_FILE,
        }
        jira_env = {
            "JIRA_URL": config.JIRA_URL,
            "JIRA_USERNAME": config.JIRA_EMAIL,
            "JIRA_API_TOKEN": config.JIRA_API_TOKEN,
        }

        sheet_params = _build_server_params(
            command=config.MCP_SHEET_COMMAND,
            extra_env=sheet_env,
        )
        jira_params = _build_server_params(
            command=config.MCP_JIRA_COMMAND,
            args=["--transport", "stdio"],
            extra_env=jira_env,
        )

        log.info("Starting MCP servers…")
        async with stdio_client(sheet_params) as (sr, sw):
            async with ClientSession(sr, sw) as sheet_sess:
                await sheet_sess.initialize()
                self.sheet_session = sheet_sess
                log.info("✓ Google Sheets MCP server connected")

                async with stdio_client(jira_params) as (jr, jw):
                    async with ClientSession(jr, jw) as jira_sess:
                        await jira_sess.initialize()
                        self.jira_session = jira_sess
                        log.info("✓ Atlassian MCP server connected")

                        await self._discover_tools()
                        yield self

    @asynccontextmanager
    async def connect_sheet_only(self):
        """Connect to the Google Sheets MCP server only."""
        sheet_env = {
            "SERVICE_ACCOUNT_PATH": config.GOOGLE_SERVICE_ACCOUNT_FILE,
        }
        sheet_params = _build_server_params(
            command=config.MCP_SHEET_COMMAND,
            extra_env=sheet_env,
        )
        async with stdio_client(sheet_params) as (sr, sw):
            async with ClientSession(sr, sw) as sheet_sess:
                await sheet_sess.initialize()
                self.sheet_session = sheet_sess
                await self._discover_sheet_tools()
                yield self

    @asynccontextmanager
    async def connect_jira_only(self):
        """Connect to the Atlassian MCP server only."""
        jira_env = {
            "JIRA_URL": config.JIRA_URL,
            "JIRA_USERNAME": config.JIRA_EMAIL,
            "JIRA_API_TOKEN": config.JIRA_API_TOKEN,
        }
        jira_params = _build_server_params(
            command=config.MCP_JIRA_COMMAND,
            args=["--transport", "stdio"],
            extra_env=jira_env,
        )
        async with stdio_client(jira_params) as (jr, jw):
            async with ClientSession(jr, jw) as jira_sess:
                await jira_sess.initialize()
                self.jira_session = jira_sess
                await self._discover_jira_tools()
                yield self

    # ── Tool discovery ──────────────────────────────────────────────────

    async def _discover_tools(self):
        """Load tool lists from both servers."""
        await self._discover_sheet_tools()
        await self._discover_jira_tools()
        log.info(
            "Discovered %d sheet tools + %d jira tools = %d total",
            len(self._sheet_tools), len(self._jira_tools),
            len(self._tool_registry),
        )

    async def _discover_sheet_tools(self):
        """Load tools from the Google Sheets MCP server."""
        resp = await self.sheet_session.list_tools()
        self._sheet_tools = resp.tools
        for tool in self._sheet_tools:
            name = f"sheet_{tool.name}"
            schema = {
                "name": name,
                "description": f"[Google Sheets] {tool.description or ''}",
                "parameters": (tool.inputSchema
                               if hasattr(tool, "inputSchema")
                               else {"type": "object", "properties": {}}),
            }
            self._tool_registry[name] = (self.sheet_session, tool.name, schema)

    async def _discover_jira_tools(self):
        """Load tools from the Atlassian MCP server.

        Tools are already namespaced (``jira_*`` / ``confluence_*``)
        because mcp-atlassian mounts sub-servers.
        """
        resp = await self.jira_session.list_tools()
        self._jira_tools = resp.tools
        for tool in self._jira_tools:
            name = tool.name  # already prefixed: jira_create_issue, etc.
            schema = {
                "name": name,
                "description": f"[Atlassian] {tool.description or ''}",
                "parameters": (tool.inputSchema
                               if hasattr(tool, "inputSchema")
                               else {"type": "object", "properties": {}}),
            }
            self._tool_registry[name] = (self.jira_session, tool.name, schema)

    # ── Tool listing helpers ────────────────────────────────────────────

    def list_tool_names(self) -> list[str]:
        """Return all registered tool names."""
        return sorted(self._tool_registry.keys())

    def get_tools_openai_format(self) -> list[dict]:
        """Return tools in OpenAI function-calling schema."""
        tools = []
        for _, (_, _, schema) in self._tool_registry.items():
            tools.append({
                "type": "function",
                "function": {
                    "name": schema["name"],
                    "description": schema["description"],
                    "parameters": schema["parameters"],
                },
            })
        return tools

    def get_tool_descriptions(self) -> str:
        """Return a human-readable list of tools (for ReAct prompts)."""
        lines = []
        for name in sorted(self._tool_registry):
            _, _, schema = self._tool_registry[name]
            desc = schema["description"]
            props = schema["parameters"].get("properties", {})
            param_names = ", ".join(props.keys()) if props else ""
            lines.append(f"  • {name}({param_names}): {desc}")
        return "\n".join(lines)

    # ── Tool execution ──────────────────────────────────────────────────

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool by its registered name and return the text result."""
        if tool_name not in self._tool_registry:
            return json.dumps({"error": f"Tool '{tool_name}' not found"})

        session, actual_name, _ = self._tool_registry[tool_name]
        log.info("MCP call: %s → %s(%s)", tool_name, actual_name, arguments)

        try:
            result = await session.call_tool(actual_name, arguments)
            # Extract text from MCP Content blocks
            texts = []
            if hasattr(result, "content"):
                for block in result.content:
                    if hasattr(block, "text"):
                        texts.append(block.text)
            return "\n".join(texts) if texts else str(result)
        except Exception as exc:
            log.exception("MCP tool call failed: %s", tool_name)
            return json.dumps({"error": f"{tool_name} failed: {exc}"})

    # Convenience aliases
    async def call_sheet_tool(self, tool_name: str, arguments: dict) -> str:
        """Call a Google Sheets tool (auto-prefix ``sheet_`` if needed)."""
        prefixed = tool_name if tool_name.startswith("sheet_") else f"sheet_{tool_name}"
        return await self.call_tool(prefixed, arguments)

    async def call_jira_tool(self, tool_name: str, arguments: dict) -> str:
        """Call a Jira tool (names already start with ``jira_``)."""
        return await self.call_tool(tool_name, arguments)
