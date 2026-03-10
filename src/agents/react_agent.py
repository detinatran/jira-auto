"""
Step 3 — ReAct Agent Loop  (Reasoning + Acting)

The agent uses the ReAct pattern:
  1. **Thought** — reason about what to do next
  2. **Action**  — call a tool
  3. **Observation** — read the tool result
  4. Repeat until the goal is achieved, then return **Final Answer**

Architecture::

    User goal
       ↓
    ┌──────────────────────────┐
    │  Thought  →  Action  →  │
    │  Observation  →  Thought │   (loop)
    │     …                    │
    │  Final Answer            │
    └──────────────────────────┘
       ↓
    Backend (MCP) → Jira / Sheet

Unlike Step 1 (native function-calling), the ReAct loop is explicitly
text-based: the LLM outputs structured Thought/Action text, and we
parse and execute it ourselves.  This gives full visibility into
the agent's reasoning chain.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Optional

from src.utils import config
from src.mcp.client import MCPClientManager

log = logging.getLogger(__name__)

MAX_STEPS   = 20        # max reasoning iterations
MAX_RETRIES = 3         # LLM rate-limit retries

# ════════════════════════════════════════════════════════════════════════════
#  ReAct prompt template
# ════════════════════════════════════════════════════════════════════════════

REACT_SYSTEM = """\
You are a ReAct agent that syncs data between Google Sheets and Jira
through MCP tools.  You solve tasks by iterating:

    Thought → Action → Observation → … → Final Answer

**Available tools:**
{tool_list}

**Output format (follow exactly):**

Thought: <reasoning about what to do next based on the observations so far>
Action: <tool_name>
Action Input: {{"key": "value"}}

After receiving an Observation, continue reasoning:

Thought: <reasoning based on what you observed>
Action: <next_tool>
Action Input: {{"key": "value"}}

When fully done:

Thought: <final reasoning>
Final Answer: <markdown summary of everything accomplished>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONTEXT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Google Sheet ID  : {sheet_id}
• Default Jira key : {default_project}
• Today's date     : {today}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GENERAL RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Always start with a Thought that reasons about the FULL plan before acting.
2. Call ONE tool per step.
3. Action Input must be a valid single-line JSON object.
4. Never fabricate data — only use what you observe from tool results.
5. If the default Jira key above is empty, call jira_get_all_projects first.
   Never guess a project key (never use PROJ, TEST, etc.)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TOOL USAGE REFERENCE (read carefully)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• sheet_get_sheet_data(spreadsheet_id, sheet)
    → Returns all rows. Use this to read headers AND count existing rows.
    → NO "max_rows" or "sheet_name" param — use "sheet" only.

• sheet_add_rows(spreadsheet_id, sheet, count)
    → ONLY inserts `count` BLANK rows at the bottom. Does NOT accept row data.
    → Use this to reserve the row, then fill it with sheet_update_cells.

• sheet_update_cells(spreadsheet_id, sheet, range, data)
    → Writes values. `range` is like "A5:L5" (NOT "values", use "data").
    → `data` is a 2-D array: [["val1", "val2", ..., "valN"]]

• jira_create_issue(project_key, summary, issue_type, ...)
    → Do NOT pass empty string "" for optional fields — omit them entirely.
    → Valid optional fields: description, assignee, priority, labels.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WORKFLOW: CREATE A NEW TASK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
When asked to create a task/issue, follow exactly these steps IN ORDER:

  [T1] Thought: Find the correct sheet tab.
       Action: sheet_list_sheets
       Action Input: {{"spreadsheet_id": "{sheet_id}"}}

  [T2] Thought: Read ALL data: learn headers (row 1) and count existing data
               rows so I know which row number is next (next_row = data_rows + 2).
       Action: sheet_get_sheet_data
       Action Input: {{"spreadsheet_id": "{sheet_id}", "sheet": "<tab>"}}

  [T3] Thought: There are N data rows, so next empty row is N+2. Add 1 blank row.
       Action: sheet_add_rows
       Action Input: {{"spreadsheet_id": "{sheet_id}", "sheet": "<tab>", "count": 1}}

  [T4] *** THIS STEP IS MANDATORY — DO NOT SKIP ***
       Thought: Blank row reserved at row N+2. I must now fill EVERY column with
               data in one sheet_update_cells call. The range spans ALL columns
               from A to the last column letter. Count the headers to find last col.
               Rules for each column type:
                 - task_id      → generate "T<4-digit>" e.g. "T0099"
                 - task_name    → the task name the user specified
                 - project_id   → use project id if known, else ""
                 - status       → "To Do"
                 - priority     → "Medium"
                 - due_date / last_updated / any date col → today YYYY-MM-DD
                 - jira_issue_key / sync_status → "" (will be filled later)
                 - assignee / reporter / description → use user-provided value or ""
               EXAMPLE — if headers are 12 columns (A to L):
                 headers: task_id|project_id|task_name|description|assignee|reporter|priority|status|due_date|jira_issue_key|sync_status|last_updated
                 data row: ["T0099","","my task","","duong","","Medium","To Do","2026-03-10","","","2026-03-10"]
       Action: sheet_update_cells
       Action Input: {{"spreadsheet_id": "{sheet_id}", "sheet": "<tab>",
                       "range": "A<N+2>:<lastColLetter><N+2>",
                       "data": [["<v1>", "<v2>", ..., "<vN>"]]}}

       *** VERIFY: data array length == number of headers. If 12 headers → 12 values. ***
  [T5] Thought: Sheet row fully written (SHEET FIRST rule satisfied).
               Now create the Jira issue. Project key = "{default_project}" if set.
               Only pass fields with real values — OMIT optional fields that are empty.
       Action: jira_create_issue
       Action Input: {{"project_key": "<key>", "summary": "<name>", "issue_type": "Task"}}

  [T6] Thought: Jira created. Issue key = <ISSUE_KEY>. Write it back into the
               jira_issue_key column of row N+2. Find that column's letter from headers.
       Action: sheet_update_cells
       Action Input: {{"spreadsheet_id": "{sheet_id}", "sheet": "<tab>",
                       "range": "<jiraKeyColLetter><N+2>",
                       "data": [["<ISSUE_KEY>"]]}}

  [T7] Final Answer: Confirm sheet row added (all columns filled) + Jira issue
       key + clickable link: {jira_url}/browse/<ISSUE_KEY>

CRITICAL RULES:
- T4 (fill row in sheet) MUST happen BEFORE T5 (jira_create_issue).
- At T4, values list length MUST equal number of headers — one value per column.
- Use today's ISO date ({today}) for any date/created/due_date/last_updated column.
"""


# ════════════════════════════════════════════════════════════════════════════
#  ReAct Agent
# ════════════════════════════════════════════════════════════════════════════

class ReactAgent:
    """Text-based ReAct (Reasoning + Acting) agent using MCP tools."""

    def __init__(self, mcp: MCPClientManager, model: Optional[str] = None):
        self._mcp = mcp

        # Pick provider
        if config.OPENAI_API_KEY:
            from openai import OpenAI
            self._provider = "openai"
            self._client = OpenAI(api_key=config.OPENAI_API_KEY)
            self._model = model or "gpt-4o-mini"
        elif config.GOOGLE_API_KEY:
            from google import genai
            self._provider = "gemini"
            self._client = genai.Client(api_key=config.GOOGLE_API_KEY)
            self._model = model or "gemini-2.5-flash"
        else:
            raise RuntimeError("No AI API key configured")

        # Build the system prompt with tool descriptions
        from datetime import date as _date
        self._system = REACT_SYSTEM.format(
            tool_list=mcp.get_tool_descriptions(),
            today=_date.today().isoformat(),
            sheet_id=config.GOOGLE_SHEET_ID,
            default_project=config.JIRA_DEFAULT_PROJECT or "(not set — call jira_get_all_projects)",
            jira_url=config.JIRA_URL.rstrip("/"),
        )

    # ── public API ──────────────────────────────────────────────────────

    async def run(self, goal: str, verbose: bool = False) -> str:
        """Execute the ReAct loop for *goal* and return the Final Answer."""
        log.info("ReAct goal: %s", goal)

        messages: list[dict] = [
            {"role": "system", "content": self._system},
            {"role": "user", "content": f"Goal: {goal}"},
        ]
        trace: list[str] = []

        # ── guard: track whether sheet row with task data was written ──
        _is_create_goal = any(k in goal.lower() for k in (
            "create", "tạo", "tao", "add task", "thêm task", "new task",
        ))
        _sheet_row_written = False   # True once sheet_update_cells with >1 value is called

        for step in range(1, MAX_STEPS + 1):
            log.info("── ReAct step %d/%d ──", step, MAX_STEPS)
            text = self._call_llm(messages)
            trace.append(text)
            if verbose:
                print(f"\n[Step {step}]\n{text}")

            # ── Final Answer? ──────────────────────────────────────────
            if "Final Answer:" in text:
                answer = text.split("Final Answer:", 1)[1].strip()
                log.info("ReAct completed in %d step(s)", step)
                return answer

            # ── Parse Action / Action Input ────────────────────────────
            action, action_input = self._parse_action(text)
            if not action:
                messages.append({"role": "assistant", "content": text})
                messages.append({
                    "role": "user",
                    "content": (
                        "I didn't see a valid Action. "
                        "Please reply with:\n"
                        "Thought: …\nAction: <tool_name>\n"
                        "Action Input: {…}\n"
                        "or\nFinal Answer: …"
                    ),
                })
                continue

            # ── Enforce: Sheet row must be written before Jira create ──
            if _is_create_goal and action == "jira_create_issue" and not _sheet_row_written:
                log.warning("Guard: jira_create_issue blocked — sheet row not written yet")
                messages.append({"role": "assistant", "content": text})
                messages.append({
                    "role": "user",
                    "content": (
                        "Observation: BLOCKED — You must complete step T4 first.\n"
                        "You have NOT yet written the full task row to the sheet.\n"
                        "Call sheet_update_cells to fill ALL columns of the new row "
                        "(task_id, task_name, status, priority, dates, etc.) "
                        "BEFORE calling jira_create_issue.\n"
                        "Go back and do step T4 now."
                    ),
                })
                continue

            # ── Track sheet_update_cells with real row data ────────────
            if action == "sheet_update_cells":
                data = action_input.get("data", [])
                if data and isinstance(data[0], list) and len(data[0]) > 1:
                    _sheet_row_written = True
                    log.info("Guard: sheet row written ✓")

            # ── Execute tool ───────────────────────────────────────────
            log.info("ReAct action: %s(%s)", action, action_input)
            observation = await self._mcp.call_tool(action, action_input)

            # Truncate huge observations so the context window stays sane
            if len(observation) > 6000:
                observation = observation[:6000] + "\n…(truncated)"

            if verbose:
                print(f"Observation: {observation[:500]}…")

            messages.append({"role": "assistant", "content": text})
            messages.append({
                "role": "user",
                "content": f"Observation:\n{observation}",
            })
            trace.append(f"Observation: {observation[:300]}…")

        return (
            "ReAct loop hit the step limit without a Final Answer.\n"
            "Last trace:\n" + "\n---\n".join(trace[-4:])
        )

    # ── LLM call ────────────────────────────────────────────────────────

    def _call_llm(self, messages: list[dict]) -> str:
        for retry in range(1, MAX_RETRIES + 1):
            try:
                if self._provider == "openai":
                    resp = self._client.chat.completions.create(
                        model=self._model,
                        messages=messages,
                        temperature=0.2,
                        max_tokens=1024,
                    )
                    return resp.choices[0].message.content or ""
                else:
                    from google.genai import types
                    contents = []
                    for m in messages:
                        if m["role"] == "system":
                            continue
                        role = "model" if m["role"] == "assistant" else "user"
                        contents.append(types.Content(
                            role=role,
                            parts=[types.Part(text=m["content"])],
                        ))
                    resp = self._client.models.generate_content(
                        model=self._model,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=self._system,
                            temperature=0.2,
                            max_output_tokens=1024,
                        ),
                    )
                    return resp.text or ""
            except Exception as exc:
                if ("429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)) and retry < MAX_RETRIES:
                    wait = 10 * retry
                    log.info("Rate limited, waiting %ds…", wait)
                    time.sleep(wait)
                    continue
                raise

    # ── Action parser ───────────────────────────────────────────────────

    @staticmethod
    def _parse_action(text: str) -> tuple[Optional[str], dict]:
        """Extract ``Action`` and ``Action Input`` from LLM text.

        Returns (tool_name, arguments_dict) or (None, {}).
        """
        action: Optional[str] = None
        action_input: dict = {}

        # Pattern:  Action: tool_name\nAction Input: {...}
        m_action = re.search(r"^Action:\s*(.+)$", text, re.MULTILINE)
        if m_action:
            candidate = m_action.group(1).strip()
            # Ensure we don't capture "Action Input" as the action name
            if "action input" not in candidate.lower():
                action = candidate

        m_input = re.search(r"Action Input:\s*(.+)", text, re.DOTALL)
        if m_input:
            raw = m_input.group(1).strip()
            # Try to extract the first JSON object
            if raw.startswith("{"):
                depth = 0
                for i, ch in enumerate(raw):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                    if depth == 0:
                        try:
                            action_input = json.loads(raw[: i + 1])
                        except json.JSONDecodeError:
                            pass
                        break
            else:
                # Maybe it's a bare string like: "Tasks"
                try:
                    action_input = json.loads(raw.split("\n")[0])
                except (json.JSONDecodeError, TypeError):
                    pass

        return action, action_input


# ════════════════════════════════════════════════════════════════════════════
#  Entry points
# ════════════════════════════════════════════════════════════════════════════

async def _run_react(goal: str, verbose: bool = False) -> str:
    async with MCPClientManager().connect() as mcp:
        agent = ReactAgent(mcp)
        return await agent.run(goal, verbose=verbose)


def run_goal(goal: str, verbose: bool = False) -> str:
    """Synchronous entry point — run a single goal through the ReAct loop."""
    return asyncio.run(_run_react(goal, verbose=verbose))


async def _interactive_loop():
    from rich.console import Console
    from rich.markdown import Markdown

    console = Console()

    async with MCPClientManager().connect() as mcp:
        agent = ReactAgent(mcp)
        prov = "OpenAI" if agent._provider == "openai" else "Gemini"

        console.print(f"[bold cyan]ReAct Agent (Step 3 — Reasoning + Acting)[/]")
        console.print(f"Provider: {prov}  |  Model: [dim]{agent._model}[/]")
        console.print(f"MCP Tools: [dim]{len(mcp.list_tool_names())} available[/]")
        console.print("Enter a goal. Type [bold]quit[/] to exit.\n")

        while True:
            try:
                goal = console.input("[bold green]Goal:[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not goal:
                continue
            if goal.lower() in ("quit", "exit", "q"):
                console.print("[dim]Goodbye![/]")
                break

            try:
                console.print("[dim]Running ReAct loop…[/]\n")
                result = await agent.run(goal, verbose=True)
                console.print()
                console.print(Markdown(result))
                console.print()
            except Exception as exc:
                log.exception("ReAct error")
                console.print(f"[bold red]Error:[/] {exc}\n")


def interactive_react():
    """Entry point — launch the Step-3 ReAct Agent interactive loop."""
    asyncio.run(_interactive_loop())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    interactive_react()
