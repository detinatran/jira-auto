#!/usr/bin/env python3
"""
test_create_task.py — Test creating a new task via MCPAgent.

Flow (Sheet-first):
  1. Agent calls sheet MCP tool to append new row to Tasks tab
  2. Agent calls jira_create_issue MCP tool to create the Jira issue
  3. Agent calls sheet MCP tool to update the row with the returned Jira key

Run:
    .venv/bin/python3 test_create_task.py 2>/dev/null
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

os.environ.setdefault("FASTMCP_LOG_LEVEL", "ERROR")
os.environ.setdefault("MCP_LOG_LEVEL", "ERROR")

logging.basicConfig(level=logging.WARNING)

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule

console = Console()


async def _run():
    from src.utils import config
    from src.mcp.client import MCPClientManager
    from src.agents.mcp_agent import MCPAgent
    from src.core import sheet_reader as sheet

    console.print(Rule("[bold cyan]Test — Create task: Sheet first → Jira via MCP[/]"))

    # ── 1. Snapshot ───────────────────────────────────────────────────────
    data_before = sheet.load_sheet()
    task_ids_before = {t.task_id for t in data_before.tasks}
    console.print(f"  Tasks in sheet before: [cyan]{len(data_before.tasks)}[/]")

    # ── 2. Connect MCP + build agent ─────────────────────────────────────
    async with MCPClientManager().connect() as mcp:
        tool_names = mcp.list_tool_names()
        console.print(f"  MCP tools available : [cyan]{len(tool_names)}[/]")

        agent = MCPAgent(mcp)
        prov = "OpenAI" if agent._provider == "openai" else "Gemini"
        console.print(f"  Provider            : [cyan]{prov} / {agent._model}[/]\n")

        # ── 3. Prompt ─────────────────────────────────────────────────────
        sheet_id = config.GOOGLE_SHEET_ID
        task_count = len(data_before.tasks)                # e.g. 15
        new_row_num = task_count + 2                        # +1 for header row, +1 for next row
        prompt = f"""
Complete this 3-step workflow using your MCP tools:

**Step 1 — Write new task row to Google Sheet (do this FIRST):**
Use sheet_update_cells to write ONE new row at row {new_row_num} of the "Tasks" tab
in spreadsheet `{sheet_id}`.
The Tasks tab has 12 columns in this exact order:
  A=task_id, B=project_id, C=task_name, D=description, E=assignee, F=reporter,
  G=priority, H=status, I=due_date, J=jira_issue_key, K=sync_status, L=last_updated

Write range "A{new_row_num}:L{new_row_num}" with these values (leave J empty for now):
  ["T_TEST_01","PROJ001","[TEST] Auto-created task via agent","Created by test_create_task.py",
   "","","Medium","To Do","2026-04-01","","Pending","2026-03-10"]

**Step 2 — Create the Jira issue:**
Call jira_create_issue with:
  project_key = KAN
  summary     = [TEST] Auto-created task via agent
  description = Created by test_create_task.py
  priority    = Medium
  due_date    = 2026-04-01

**Step 3 — Update the sheet row with the Jira key:**
Take the issue key returned from step 2 and write it into cell J{new_row_num}
(column J = jira_issue_key). Also set K{new_row_num} = "Synced".

Report the Jira key created and confirm each step completed.
"""
        console.print(Panel(prompt.strip(), title="Prompt to MCPAgent", border_style="dim"))
        console.print("\n[dim]Running agent…[/]\n")

        reply = await agent.send(prompt)

    # ── 4. Show reply ─────────────────────────────────────────────────────
    console.print(Panel(Markdown(reply), title="Agent reply", border_style="green"))

    # ── 5. Verify sheet ───────────────────────────────────────────────────
    console.print("\n[dim]Reloading sheet to verify…[/]")
    data_after = sheet.load_sheet()
    new_ids = {t.task_id for t in data_after.tasks} - task_ids_before

    console.print(Rule("[bold]Verification[/]"))
    console.print(f"  Tasks in sheet after : [cyan]{len(data_after.tasks)}[/]")

    if new_ids:
        console.print(f"  New task IDs        : [green]{', '.join(sorted(new_ids))}[/]")
        for t in data_after.tasks:
            if t.task_id in new_ids:
                console.print(
                    f"  Row: task_id=[bold]{t.task_id}[/]  "
                    f"jira_key=[bold]{t.jira_issue_key or 'N/A'}[/]  "
                    f"sync_status={t.sync_status}"
                )
        console.print("\n[bold green]  PASS — task written to Sheet and Jira via MCP.[/]")
    else:
        console.print("[bold yellow]  WARNING — T_TEST_01 not found in Tasks tab.[/]")
        console.print("  Check agent reply above for details.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(_run())
