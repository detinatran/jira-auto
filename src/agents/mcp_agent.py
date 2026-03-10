"""
Step 1 — Agent-Driven Architecture  (LLM → MCP → Service)

The LLM agent connects to both Google Sheets and Jira through MCP servers.
It **autonomously decides** which tools to call based on user requests,
using OpenAI or Gemini function-calling.

Architecture diagram::

    User
      ↓  (natural-language request)
    LLM Agent  ──function-calling──→  MCP Protocol
                                        ├── Google Sheets MCP Server
                                        └── Atlassian (Jira) MCP Server

This is the most flexible approach: the agent can chain arbitrary
tool calls (read sheet → transform → create issues → verify).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from src.utils import config
from src.mcp.client import MCPClientManager

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 15          # safety cap on function-call iterations
MAX_RETRIES     = 3           # rate-limit retries

# ════════════════════════════════════════════════════════════════════════════
#  System prompt
# ════════════════════════════════════════════════════════════════════════════

SYSTEM_INSTRUCTION = """\
You are **Jira-Sheet Sync Agent**, an AI that automates workflows between
Google Sheets and Jira using MCP (Model Context Protocol) tools.

**Available tool groups:**
• Google Sheets tools (prefixed ``sheet_``): read/write spreadsheet data.
• Jira tools (prefixed ``jira_``): create, update, search and manage issues.

**Common workflows you can execute:**
1. *Sync sheet → Jira*:  Read rows from a sheet, then create Jira issues.
2. *Check Jira status*:  Search Jira issues by JQL and report back.
3. *Update sheet from Jira*: Pull Jira data and write it into the sheet.
4. *Create new task*: Create in Jira AND record in the Google Sheet.

**Guidelines:**
1. Always call tools to get real data — never guess or fabricate.
2. For sync operations, read the sheet first, then create/update Jira issues.
3. Report results clearly with issue keys and status.
4. If a tool returns an error, explain what went wrong.
5. Google Sheet ID: ``{sheet_id}``
6. Default Jira project key: ``{default_project}``

**CRITICAL — Jira project key rules:**
- NEVER guess or invent a project key (e.g. do NOT use 'PROJ', 'TEST', etc.).
- If a default project key is provided above (non-empty), use it directly.
- If no default is set, you MUST call ``jira_get_all_projects`` first to
  discover available projects, then ask the user to confirm which one to use
  (or pick the first/only one automatically if there is exactly one result).
- Only proceed with ``jira_create_issue`` once you have a confirmed real key.

**CRITICAL — "Create new task" workflow (ALWAYS follow these steps in order):**
When the user asks to create a new task or issue, you MUST follow this exact
sequence — Sheet first, Jira second:
  Step 1. Call ``sheet_list_sheets`` on spreadsheet_id=``{sheet_id}`` to find
          the right sheet (look for names like Tasks, Sheet1, Công việc, etc.).
  Step 2. Call ``sheet_get_sheet_data`` to read the first ~5 rows and discover
          the exact column headers (e.g. Task ID, Summary, Status, Jira Key…).
  Step 3. Call ``sheet_add_rows`` to append a new row with the task info.
          At minimum include: task name/summary, status="To Do", today's date.
          Leave the Jira key column empty ("") for now — it will be filled next.
  Step 4. Call ``jira_create_issue`` (using a valid project key per the rules
          above). Note the returned issue key (e.g. KAN-42).
  Step 5. Call ``sheet_find_in_spreadsheet`` or ``sheet_get_sheet_data`` to
          locate the row just added, then call ``sheet_update_cells`` to fill in
          the Jira issue key column with the key from Step 4.
  Step 6. Report back to the user confirming: row added to sheet AND Jira issue
          created, with the issue key and link.
- NEVER skip any step. Sheet must be written BEFORE Jira is called.
"""


# ════════════════════════════════════════════════════════════════════════════
#  Agent class
# ════════════════════════════════════════════════════════════════════════════

class MCPAgent:
    """LLM agent that discovers and calls MCP tools from both servers."""

    def __init__(
        self,
        mcp: MCPClientManager,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ):
        self._mcp = mcp

        # ── pick LLM provider ──────────────────────────────────────────
        if provider is None:
            if config.OPENAI_API_KEY:
                provider = "openai"
            elif config.GOOGLE_API_KEY:
                provider = "gemini"
            else:
                raise RuntimeError(
                    "No AI API key configured. "
                    "Set OPENAI_API_KEY or GOOGLE_API_KEY in .env"
                )
        self._provider = provider

        system = SYSTEM_INSTRUCTION.format(
            sheet_id=config.GOOGLE_SHEET_ID,
            default_project=config.JIRA_DEFAULT_PROJECT or "(not set — call jira_get_all_projects first)",
        )

        if provider == "openai":
            from openai import OpenAI
            self._model = model or "gpt-4o-mini"
            self._client = OpenAI(api_key=config.OPENAI_API_KEY)
            self._messages: list[dict] = [
                {"role": "system", "content": system},
            ]
            log.info("MCPAgent: OpenAI %s", self._model)

        elif provider == "gemini":
            from google import genai
            self._model = model or "gemini-2.5-flash"
            self._genai = genai.Client(api_key=config.GOOGLE_API_KEY)
            self._system = system
            self._messages: list[dict] = []
            log.info("MCPAgent: Gemini %s", self._model)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    # ── public API ──────────────────────────────────────────────────────

    async def send(self, message: str) -> str:
        """Send a user message and return the final assistant reply."""
        if self._provider == "openai":
            return await self._send_openai(message)
        return await self._send_gemini(message)

    def reset(self):
        """Clear conversation history."""
        system = SYSTEM_INSTRUCTION.format(
            sheet_id=config.GOOGLE_SHEET_ID,
            default_project=config.JIRA_DEFAULT_PROJECT or "(not set — call jira_get_all_projects first)",
        )
        if self._provider == "openai":
            self._messages = [{"role": "system", "content": system}]
        else:
            self._messages = []

    # ── OpenAI implementation ───────────────────────────────────────────

    async def _send_openai(self, message: str) -> str:
        self._messages.append({"role": "user", "content": message})
        tools = self._mcp.get_tools_openai_format()

        for _ in range(MAX_TOOL_ROUNDS):
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=self._messages,
                tools=tools or None,
                temperature=0.2,
            )
            msg = resp.choices[0].message

            if not msg.tool_calls:
                self._messages.append(
                    {"role": "assistant", "content": msg.content}
                )
                return msg.content or "(no response)"

            # Execute each tool call
            self._messages.append(msg)
            for tc in msg.tool_calls:
                name = tc.function.name
                args = json.loads(tc.function.arguments)
                log.info("OpenAI → %s(%s)", name, args)
                result = await self._mcp.call_tool(name, args)
                self._messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        return "(max tool-call rounds reached)"

    # ── Gemini implementation ───────────────────────────────────────────

    async def _send_gemini(self, message: str) -> str:
        from google.genai import types

        self._messages.append({"role": "user", "content": message})

        # Build function declarations from MCP tools
        declarations = []
        for name in self._mcp.list_tool_names():
            _, _, schema = self._mcp._tool_registry[name]
            params = schema.get("parameters", {})
            clean = {"type": "object", "properties": params.get("properties", {})}
            if "required" in params:
                clean["required"] = params["required"]
            declarations.append(types.FunctionDeclaration(
                name=schema["name"],
                description=schema["description"],
                parameters=clean,
            ))

        gemini_tools = [types.Tool(function_declarations=declarations)]

        # Rebuild contents
        contents = []
        for m in self._messages:
            role = "model" if m["role"] == "assistant" else "user"
            contents.append(types.Content(
                role=role,
                parts=[types.Part(text=m["content"])],
            ))

        for attempt in range(MAX_TOOL_ROUNDS):
            for retry in range(1, MAX_RETRIES + 1):
                try:
                    resp = self._genai.models.generate_content(
                        model=self._model,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=self._system,
                            tools=gemini_tools,
                            temperature=0.2,
                        ),
                    )
                    break
                except Exception as exc:
                    if ("429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)) and retry < MAX_RETRIES:
                        wait = 10 * retry
                        log.info("Rate limited, waiting %ds…", wait)
                        time.sleep(wait)
                        continue
                    raise

            # Inspect for function calls
            has_fc = False
            fc_parts: list = []
            fr_parts: list = []

            for cand in resp.candidates:
                for part in cand.content.parts:
                    if hasattr(part, "function_call") and part.function_call:
                        has_fc = True
                        fc = part.function_call
                        args = dict(fc.args) if fc.args else {}
                        log.info("Gemini → %s(%s)", fc.name, args)
                        result = await self._mcp.call_tool(fc.name, args)
                        fc_parts.append(part)
                        fr_parts.append(types.Part(
                            function_response=types.FunctionResponse(
                                name=fc.name,
                                response={"result": result},
                            )
                        ))

            if not has_fc:
                text = resp.text or "(no response)"
                self._messages.append({"role": "assistant", "content": text})
                return text

            contents.append(resp.candidates[0].content)
            contents.append(types.Content(role="user", parts=fr_parts))

        return "(max tool-call rounds reached)"


# ════════════════════════════════════════════════════════════════════════════
#  Interactive REPL
# ════════════════════════════════════════════════════════════════════════════

async def _interactive_loop():
    from rich.console import Console
    from rich.markdown import Markdown

    console = Console()

    async with MCPClientManager().connect() as mcp:
        agent = MCPAgent(mcp)
        prov = "OpenAI" if agent._provider == "openai" else "Gemini"

        console.print(f"[bold cyan]MCP Agent (Step 1 — Agent-Driven)[/]")
        console.print(f"Provider: {prov}  |  Model: [dim]{agent._model}[/]")
        console.print(f"MCP Tools: [dim]{len(mcp.list_tool_names())} available[/]")
        console.print(
            "Type [bold]quit[/] to exit  |  "
            "[bold]/tools[/] list tools  |  "
            "[bold]/reset[/] new conversation\n"
        )

        while True:
            try:
                user = console.input("[bold green]You:[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user:
                continue
            if user.lower() in ("quit", "exit", "q"):
                console.print("[dim]Goodbye![/]")
                break
            if user.lower() == "/tools":
                for n in mcp.list_tool_names():
                    console.print(f"  • {n}")
                console.print()
                continue
            if user.lower() == "/reset":
                agent.reset()
                console.print("[dim]Conversation reset.[/]\n")
                continue

            try:
                reply = await agent.send(user)
                console.print()
                console.print(Markdown(reply))
                console.print()
            except Exception as exc:
                log.exception("Agent error")
                console.print(f"[bold red]Error:[/] {exc}\n")


def interactive_chat():
    """Entry point — launch the Step-1 MCP Agent interactive chat."""
    asyncio.run(_interactive_loop())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    interactive_chat()
