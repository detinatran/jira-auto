"""
sync_service.py — Orchestrate sync between Google Sheets and Jira Cloud.

Flow
────
1. Read pending tasks from the sheet.
2. For each task:
   a. If it has no ``jira_issue_key`` → **create** in Jira.
   b. If it already has a key → **update** the Jira issue.
3. Write the resulting Jira key and sync status back to the sheet.
4. Append an entry to the Sync Log.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

import config
import jira_client as jira
import sheet_reader as sheet
from sheet_reader import (
    SheetData,
    SyncLogEntry,
    Task,
    TaskUpdate,
)

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════
#  Caches
# ════════════════════════════════════════════════════════════════════════════

# Cache resolved Jira account IDs so we don't look up the same person twice
_account_id_cache: dict[str, str | None] = {}


def _resolve_assignee(name: str, project_key: str) -> str | None:
    """
    Attempt to find a real Jira accountId for *name*.
    Falls back to None (unassigned) if the lookup fails.
    """
    if name in _account_id_cache:
        return _account_id_cache[name]

    try:
        users = jira.find_assignable_users(project_key, query=name)
        if users:
            account_id = users[0].get("accountId")
            _account_id_cache[name] = account_id
            return account_id
    except Exception as exc:
        log.warning("Assignee lookup failed for %s: %s", name, exc)

    _account_id_cache[name] = None
    return None

# ════════════════════════════════════════════════════════════════════════════
#  ID generators (simple sequential for the workbook)
# ════════════════════════════════════════════════════════════════════════════

def _next_log_id(data: SheetData) -> str:
    nums = [int(e.log_id.replace("L", "")) for e in data.sync_log if e.log_id.startswith("L")]
    nxt = max(nums, default=0) + 1
    return f"L{nxt:03d}"


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


# ════════════════════════════════════════════════════════════════════════════
#  Core sync logic
# ════════════════════════════════════════════════════════════════════════════

def sync_task_to_jira(task: Task, data: SheetData) -> dict:
    """
    Sync a single task to Jira.

    Returns a dict with keys:
        action   – "create" | "update"
        success  – bool
        issue_key – Jira key if successful
        message  – human-readable status
    """
    project_key = sheet.get_project_key_for_task(task, data)
    if not project_key:
        return {
            "action": "skip",
            "success": False,
            "issue_key": None,
            "message": f"No Jira project key found for project {task.project_id}",
        }

    # Resolve assignee & reporter — look up real Jira accountIds by name
    assignee_id = _resolve_assignee(task.assignee, project_key) if task.assignee else None
    reporter_id = _resolve_assignee(task.reporter, project_key) if task.reporter else None

    # ── CREATE ──────────────────────────────────────────────────────────
    if not task.jira_issue_key:
        log.info("Creating Jira issue for %s: %s", task.task_id, task.task_name)
        resp = jira.create_issue(
            project_key=project_key,
            summary=task.task_name,
            description=task.description,
            assignee_account_id=assignee_id,
            reporter_account_id=reporter_id,
            priority=task.priority,
            due_date=task.due_date if task.due_date else None,
        )
        if "error" in resp:
            return {
                "action": "create",
                "success": False,
                "issue_key": None,
                "message": resp.get("message", str(resp)),
            }
        issue_key = resp.get("key", "")

        # Transition to the correct status (e.g. In Progress, Done, Blocked)
        if task.status and task.status.strip().lower() != "to do":
            trans_resp = jira.transition_issue(issue_key, task.status)
            if "error" in trans_resp:
                log.warning("Transition to '%s' failed for %s: %s",
                            task.status, issue_key, trans_resp.get("message"))

        return {
            "action": "create",
            "success": True,
            "issue_key": issue_key,
            "message": f"Issue {issue_key} created successfully",
        }

    # ── UPDATE ──────────────────────────────────────────────────────────
    issue_key = task.jira_issue_key
    log.info("Updating Jira issue %s for %s", issue_key, task.task_id)

    fields_to_update: dict = {
        "summary": task.task_name,
        "priority": {"name": jira._normalise_priority(task.priority)},
    }
    if assignee_id:
        fields_to_update["assignee"] = {"accountId": assignee_id}
    if reporter_id:
        fields_to_update["reporter"] = {"accountId": reporter_id}
    if task.description:
        fields_to_update["description"] = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": task.description}],
                }
            ],
        }

    resp = jira.update_issue(issue_key, fields=fields_to_update)
    if "error" in resp:
        return {
            "action": "update",
            "success": False,
            "issue_key": issue_key,
            "message": resp.get("message", str(resp)),
        }

    # Transition to the correct status (e.g. In Progress, Done, Blocked)
    if task.status and task.status.strip().lower() != "to do":
        trans_resp = jira.transition_issue(issue_key, task.status)
        if "error" in trans_resp:
            log.warning("Transition to '%s' failed for %s: %s",
                        task.status, issue_key, trans_resp.get("message"))

    return {
        "action": "update",
        "success": True,
        "issue_key": issue_key,
        "message": f"Issue {issue_key} updated successfully",
    }


# ════════════════════════════════════════════════════════════════════════════
#  Batch sync
# ════════════════════════════════════════════════════════════════════════════

def run_sync() -> list[dict]:
    """
    Run a full sync of all **Pending** tasks to Jira, write back
    results to the Google Sheet, and return a list of per-task result dicts.
    """
    if not config.validate_jira_config():
        log.warning("Jira credentials not configured — running in dry-run mode")
        return _dry_run()

    data = sheet.load_sheet()
    pending = [t for t in data.tasks if t.sync_status.lower() == "pending"]

    if not pending:
        log.info("No pending tasks to sync.")
        return []

    results: list[dict] = []

    for task in pending:
        result = sync_task_to_jira(task, data)
        results.append({"task_id": task.task_id, **result})

        # Persist to sheet
        updates: dict = {
            "sync_status": "Synced" if result["success"] else "Failed",
            "last_updated": _now(),
        }
        if result.get("issue_key") and not task.jira_issue_key:
            updates["jira_issue_key"] = result["issue_key"]
        sheet.update_task_in_sheet(task.task_id, updates)

        # Append to Sync Log
        log_entry = SyncLogEntry(
            log_id=_next_log_id(data),
            task_id=task.task_id,
            action=result["action"],
            jira_issue=result.get("issue_key") or task.jira_issue_key,
            status="success" if result["success"] else "failed",
            message=result["message"],
            timestamp=_now(),
        )
        sheet.append_sync_log(log_entry)
        # Keep data.sync_log up to date for next _next_log_id call
        data.sync_log.append(log_entry)

    return results


# ════════════════════════════════════════════════════════════════════════════
#  Dry-run (when Jira creds are absent)
# ════════════════════════════════════════════════════════════════════════════

def _dry_run() -> list[dict]:
    """Simulate a sync without actually calling Jira."""
    data = sheet.load_sheet()
    pending = [t for t in data.tasks if t.sync_status.lower() == "pending"]
    results = []
    for task in pending:
        action = "create" if not task.jira_issue_key else "update"
        project_key = sheet.get_project_key_for_task(task, data)
        fake_key = f"{project_key}-DRY" if action == "create" else task.jira_issue_key
        results.append({
            "task_id": task.task_id,
            "action": action,
            "success": True,
            "issue_key": fake_key,
            "message": f"[DRY RUN] Would {action} {fake_key}",
        })
    return results


# ════════════════════════════════════════════════════════════════════════════
#  CLI quick test
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    results = run_sync()
    print(json.dumps(results, indent=2))
