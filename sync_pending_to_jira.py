"""
sync_pending_to_jira.py — Sync Pending sheet tasks → Jira via MCP.

Reads the Tasks tab, finds every row that has no jira_issue_key
(or sync_status != "Synced"), creates a Jira issue for each one
via the Atlassian MCP server, then writes the resulting key and
"Synced" status back into the Google Sheet.

Usage:
    python sync_pending_to_jira.py [--project-key KAN] [--dry-run]

Options:
    --project-key  Jira project key to create issues in (default: KAN)
    --dry-run      Print what would be synced without actually doing it
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import sys

# ── bootstrap path ──────────────────────────────────────────────────────────
sys.path.insert(0, "/Users/meomeo/Documents/jira")

from src.core import sheet_reader as sheet
from src.core.sheet_reader import Task
from src.mcp.client import MCPClientManager
from src.utils import config

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s  %(message)s",
)
log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════
#  Priority mapping  (sheet value → Jira)
# ════════════════════════════════════════════════════════════════════════════

PRIORITY_MAP = {
    "critical": "Highest",
    "high":     "High",
    "medium":   "Medium",
    "low":      "Low",
    "lowest":   "Lowest",
}


def _jira_priority(raw: str) -> str:
    return PRIORITY_MAP.get(raw.strip().lower(), "Medium")


# ════════════════════════════════════════════════════════════════════════════
#  Core sync logic
# ════════════════════════════════════════════════════════════════════════════

async def _create_jira_issue(mcp: MCPClientManager, task: Task, project_key: str) -> str | None:
    """Call jira_create_issue via MCP and return the new issue key (or None)."""
    import re

    description = task.description or f"Task: {task.task_name}"
    parts = []
    if task.reporter:
        parts.append(f"Reporter: {task.reporter}")
    if task.due_date:
        parts.append(f"Due: {task.due_date}")
    if task.task_id:
        parts.append(f"Sheet task ID: {task.task_id}")
    if parts:
        description += "\n\n" + "\n".join(parts)

    # priority must go into additional_fields (not a top-level param)
    additional = json.dumps({"priority": {"name": _jira_priority(task.priority)}})

    kwargs: dict = {
        "project_key":       project_key,
        "summary":           task.task_name,
        "description":       description,
        "issue_type":        "Task",
        "additional_fields": additional,
    }
    if task.assignee and task.assignee.strip():
        kwargs["assignee"] = task.assignee.strip()

    raw = await mcp.call_tool("jira_create_issue", kwargs)

    # mcp-atlassian returns {"message": "...", "issue": {"key": "KAN-32", ...}}
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(payload, dict):
            # Nested under "issue"
            if "issue" in payload and isinstance(payload["issue"], dict):
                return payload["issue"].get("key") or payload["issue"].get("id")
            # Flat (fallback)
            return payload.get("key") or payload.get("id")
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: scan for an issue key pattern like KAN-32
    if isinstance(raw, str):
        m = re.search(r"\b([A-Z][A-Z0-9]+-\d+)\b", raw)
        if m:
            return m.group(1)

    return None


async def _sync(project_key: str, dry_run: bool) -> None:
    data = sheet.load_sheet()
    pending = [
        t for t in data.tasks
        if not t.jira_issue_key or t.jira_issue_key.strip() == "" or t.sync_status != "Synced"
    ]

    print(f"Sheet tasks  : {len(data.tasks)}")
    print(f"Pending sync : {len(pending)}")
    print(f"Project key  : {project_key}")
    if dry_run:
        print("[DRY RUN] — no changes will be made")
    print()

    if not pending:
        print("Nothing to sync — all tasks already have Jira issues.")
        return

    if dry_run:
        print("Tasks that WOULD be synced:")
        for t in pending:
            print(f"  {t.task_id:<14} | {t.priority:<8} | {t.task_name}")
        return

    ok_count  = 0
    fail_count = 0
    results: list[dict] = []

    async with MCPClientManager().connect() as mcp:
        tool_names = mcp.list_tool_names()
        if "jira_create_issue" not in tool_names:
            print("[ERROR] jira_create_issue not available — check JIRA credentials in .env")
            return

        print(f"MCP tools loaded : {len(tool_names)}")
        print()

        for idx, task in enumerate(pending, 1):
            print(f"[{idx}/{len(pending)}] {task.task_id}: {task.task_name[:50]}")
            try:
                jira_key = await _create_jira_issue(mcp, task, project_key)
                if jira_key:
                    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    sheet.update_task_in_sheet(task.task_id, {
                        "jira_issue_key": jira_key,
                        "sync_status":    "Synced",
                        "last_updated":   now,
                    })
                    print(f"         [OK] Created {jira_key} — sheet updated")
                    ok_count += 1
                    results.append({"task_id": task.task_id, "jira_key": jira_key, "status": "ok"})
                else:
                    print(f"         [FAIL] No issue key returned from Jira")
                    print(f"                (enable DEBUG logging for raw response)")
                    fail_count += 1
                    results.append({"task_id": task.task_id, "jira_key": None, "status": "no_key"})
            except Exception as exc:
                print(f"         [FAIL] {exc}")
                fail_count += 1
                results.append({"task_id": task.task_id, "jira_key": None, "status": str(exc)})

    print()
    print("=" * 50)
    print(f"Done. Synced: {ok_count}  Failed: {fail_count}")
    if ok_count:
        synced = [r for r in results if r["status"] == "ok"]
        print("Created Jira issues:")
        for r in synced:
            print(f"  {r['task_id']} → {r['jira_key']}")


# ════════════════════════════════════════════════════════════════════════════
#  CLI entry point
# ════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="Sync pending sheet tasks → Jira via MCP")
    parser.add_argument(
        "--project-key", "-p",
        default="KAN",
        help="Jira project key (default: KAN)",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Preview without making changes",
    )
    args = parser.parse_args()

    asyncio.run(_sync(project_key=args.project_key, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
