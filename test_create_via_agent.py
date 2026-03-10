"""
test_create_via_agent.py — Test if MCPAgent can create tasks end-to-end:
  1. Create new task in Google Sheet
  2. Create Jira issue via MCP
  3. Update sheet with jira_key

Flow (MCPAgent should decide):
  → Read sheet to check existing tasks
  → Use sheet_append_row or similar to add new task
  → Use jira_create_issue to create in Jira
  → Update sheet with the resulting jira_key
"""

import asyncio
import sys

sys.path.insert(0, "/Users/meomeo/Documents/jira")

from src.agents.mcp_agent import MCPAgent
from src.mcp.client import MCPClientManager


async def test_create_task_via_agent() -> None:
    """Test if agent can create task autonomously."""

    # Create 2 pending tasks for the test
    from src.core import sheet_reader as sheet
    from src.core.sheet_reader import Task
    import datetime

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    test_tasks = [
        Task(
            task_id="T_AGENT_01",
            project_id="P001",
            task_name="Implement WebSocket support",
            description="Add real-time WebSocket connection for live updates.",
            assignee="",
            reporter="",
            priority="High",
            status="To Do",
            due_date="2026-05-01",
            jira_issue_key="",
            sync_status="Pending",
            last_updated=now,
        ),
        Task(
            task_id="T_AGENT_02",
            project_id="P001",
            task_name="Setup Redis caching layer",
            description="Implement Redis cache for performance optimization.",
            assignee="",
            reporter="",
            priority="Medium",
            status="To Do",
            due_date="2026-05-15",
            jira_issue_key="",
            sync_status="Pending",
            last_updated=now,
        ),
    ]

    # Add to sheet
    print("[SETUP] Adding pending tasks to sheet...")
    for t in test_tasks:
        sheet.append_new_task(t)

    # Verify they're pending
    data = sheet.load_sheet()
    pending = [task for task in data.tasks if not task.jira_issue_key or task.sync_status != "Synced"]
    print(f"[SETUP] Now have {len(pending)} pending tasks to sync")
    print()

    # Initialize agent
    print("[AGENT] Initializing MCPAgent...")
    async with MCPClientManager().connect() as mcp:
        agent = MCPAgent(mcp, provider="openai")

        # Prompt agent to sync sheet tasks to Jira
        prompt = """\
I have 2 new pending tasks in my Google Sheet (task IDs: T_AGENT_01, T_AGENT_02).
Please:
1. Read the Tasks tab from the Google Sheet (ID: 1IMWZ3POaPCHt2GqaO7QtRMLKCpcPcTXEO1Q2WwH-uZY)
2. Find all tasks with sync_status != "Synced"
3. For each one, create a Jira issue in project KAN
4. Update the sheet with the resulting Jira issue keys and mark as "Synced"

Report back with the issue keys created.
"""

        print(f"[AGENT] Sending prompt...")
        print(f"        {prompt[:80]}...")
        print()

        reply = await agent.send(prompt)
        print(f"[AGENT] Reply:")
        print(reply)
        print()

    # Verify sheet was updated
    print("[VERIFY] Checking sheet after agent run...")
    data_after = sheet.load_sheet()
    for t in test_tasks:
        updated = next((task for task in data_after.tasks if task.task_id == t.task_id), None)
        if updated:
            print(
                f"  {updated.task_id}: jira_key={updated.jira_issue_key} "
                f"sync_status={updated.sync_status}"
            )
        else:
            print(f"  {t.task_id}: NOT FOUND in sheet")


if __name__ == "__main__":
    asyncio.run(test_create_task_via_agent())
