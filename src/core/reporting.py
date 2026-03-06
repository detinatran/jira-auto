"""
reporting.py — Analytics & reporting from the Sheet / Jira data.

Generates rich terminal tables and summary reports using data already
in the Excel workbook.  Can also pull live data from Jira when creds
are available.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections import Counter

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from src.utils import config
from src.core import jira_client as jira
from src.core import sheet_reader as sheet
from sheet_reader import SheetData

log = logging.getLogger(__name__)
console = Console()


# ════════════════════════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════════════════════════

def _today() -> str:
    return dt.date.today().isoformat()


def _is_overdue(due: str, status: str) -> bool:
    if not due or status.lower() == "done":
        return False
    try:
        due_date = dt.date.fromisoformat(due[:10])
        return due_date < dt.date.today()
    except ValueError:
        return False


# ════════════════════════════════════════════════════════════════════════════
#  Report generators
# ════════════════════════════════════════════════════════════════════════════

def print_dashboard(data: SheetData | None = None) -> None:
    """Print a high-level dashboard to the terminal."""
    if data is None:
        data = sheet.load_sheet()

    summary = sheet.sheet_summary(data)
    overdue = [t for t in data.tasks if _is_overdue(t.due_date, t.status)]

    # Header panel
    console.print(Panel(
        f"[bold]Total Projects:[/] {summary['total_projects']}  |  "
        f"[bold]Total Tasks:[/] {summary['total_tasks']}  |  "
        f"[bold]Pending Sync:[/] {summary['pending_sync']}  |  "
        f"[bold]Overdue:[/] [red]{len(overdue)}[/]",
        title="📊 Project Dashboard",
        border_style="cyan",
    ))

    # Status breakdown
    tbl = Table(title="Tasks by Status", box=box.ROUNDED)
    tbl.add_column("Status", style="bold")
    tbl.add_column("Count", justify="right")
    for status, count in sorted(summary["tasks_by_status"].items()):
        color = {
            "Done": "green", "In Progress": "yellow",
            "Blocked": "red", "To Do": "dim",
        }.get(status, "white")
        tbl.add_row(f"[{color}]{status}[/]", str(count))
    console.print(tbl)
    console.print()


def print_task_table(data: SheetData | None = None) -> None:
    """Print a detailed task table."""
    if data is None:
        data = sheet.load_sheet()

    tbl = Table(title="All Tasks", box=box.ROUNDED, show_lines=True)
    tbl.add_column("ID", style="dim")
    tbl.add_column("Project", style="cyan")
    tbl.add_column("Task", style="bold")
    tbl.add_column("Assignee")
    tbl.add_column("Priority")
    tbl.add_column("Status")
    tbl.add_column("Due Date")
    tbl.add_column("Jira Key")
    tbl.add_column("Sync")

    for t in data.tasks:
        priority_color = {
            "Critical": "bold red", "High": "red",
            "Medium": "yellow", "Low": "dim",
        }.get(t.priority, "white")
        status_color = {
            "Done": "green", "In Progress": "yellow",
            "Blocked": "red", "To Do": "dim",
        }.get(t.status, "white")
        overdue_marker = " ⚠️" if _is_overdue(t.due_date, t.status) else ""

        tbl.add_row(
            t.task_id,
            t.project_id,
            t.task_name,
            t.assignee,
            f"[{priority_color}]{t.priority}[/]",
            f"[{status_color}]{t.status}[/]",
            f"{t.due_date}{overdue_marker}",
            t.jira_issue_key or "—",
            t.sync_status,
        )

    console.print(tbl)
    console.print()


def print_project_table(data: SheetData | None = None) -> None:
    """Print a project overview table."""
    if data is None:
        data = sheet.load_sheet()

    tbl = Table(title="Projects", box=box.ROUNDED)
    tbl.add_column("ID", style="dim")
    tbl.add_column("Name", style="bold")
    tbl.add_column("Jira Key", style="cyan")
    tbl.add_column("Owner")
    tbl.add_column("Status")
    tbl.add_column("Tasks", justify="right")
    tbl.add_column("Done", justify="right")

    for p in data.projects:
        proj_tasks = [t for t in data.tasks if t.project_id == p.project_id]
        done = sum(1 for t in proj_tasks if t.status.lower() == "done")
        tbl.add_row(
            p.project_id,
            p.project_name,
            p.jira_project_key,
            p.owner,
            p.status,
            str(len(proj_tasks)),
            str(done),
        )

    console.print(tbl)
    console.print()


def print_team_workload(data: SheetData | None = None) -> None:
    """Print per-team-member workload."""
    if data is None:
        data = sheet.load_sheet()

    tbl = Table(title="Team Workload", box=box.ROUNDED)
    tbl.add_column("Member", style="bold")
    tbl.add_column("Role")
    tbl.add_column("Total", justify="right")
    tbl.add_column("In Progress", justify="right", style="yellow")
    tbl.add_column("To Do", justify="right")
    tbl.add_column("Done", justify="right", style="green")
    tbl.add_column("Blocked", justify="right", style="red")
    tbl.add_column("Overdue", justify="right", style="bold red")

    for m in data.team_members:
        tasks = [t for t in data.tasks if t.assignee.lower() == m.name.lower()]
        by_status = Counter(t.status for t in tasks)
        overdue = sum(1 for t in tasks if _is_overdue(t.due_date, t.status))
        tbl.add_row(
            m.name,
            m.role,
            str(len(tasks)),
            str(by_status.get("In Progress", 0)),
            str(by_status.get("To Do", 0)),
            str(by_status.get("Done", 0)),
            str(by_status.get("Blocked", 0)),
            str(overdue),
        )

    console.print(tbl)
    console.print()


def print_sync_log(data: SheetData | None = None, limit: int = 20) -> None:
    """Print the most recent sync-log entries."""
    if data is None:
        data = sheet.load_sheet()

    entries = data.sync_log[-limit:]

    tbl = Table(title="Recent Sync Log", box=box.ROUNDED)
    tbl.add_column("Log ID", style="dim")
    tbl.add_column("Task")
    tbl.add_column("Action")
    tbl.add_column("Jira Issue", style="cyan")
    tbl.add_column("Status")
    tbl.add_column("Message")
    tbl.add_column("Timestamp")

    for e in entries:
        status_style = "green" if e.status == "success" else "red"
        tbl.add_row(
            e.log_id, e.task_id, e.action, e.jira_issue or "—",
            f"[{status_style}]{e.status}[/]", e.message, e.timestamp,
        )

    console.print(tbl)
    console.print()


def print_overdue_tasks(data: SheetData | None = None) -> None:
    """Print tasks that are past their due date and not Done."""
    if data is None:
        data = sheet.load_sheet()

    overdue = [t for t in data.tasks if _is_overdue(t.due_date, t.status)]

    if not overdue:
        console.print("[green]✅ No overdue tasks![/]\n")
        return

    tbl = Table(title="⚠️  Overdue Tasks", box=box.ROUNDED, border_style="red")
    tbl.add_column("Task ID", style="dim")
    tbl.add_column("Task", style="bold")
    tbl.add_column("Assignee")
    tbl.add_column("Due Date", style="red")
    tbl.add_column("Status")
    tbl.add_column("Project")

    for t in overdue:
        tbl.add_row(
            t.task_id, t.task_name, t.assignee,
            t.due_date, t.status, t.project_id,
        )

    console.print(tbl)
    console.print()


def full_report(data: SheetData | None = None) -> None:
    """Print all reports in sequence."""
    if data is None:
        data = sheet.load_sheet()

    console.print()
    print_dashboard(data)
    print_project_table(data)
    print_task_table(data)
    print_team_workload(data)
    print_overdue_tasks(data)
    print_sync_log(data)


# ── JSON export ─────────────────────────────────────────────────────────────

def export_report_json(data: SheetData | None = None) -> str:
    """Return the full report as a JSON string (for dashboards / BI tools)."""
    if data is None:
        data = sheet.load_sheet()

    summary = sheet.sheet_summary(data)
    overdue = [
        {"task_id": t.task_id, "task_name": t.task_name,
         "assignee": t.assignee, "due_date": t.due_date, "status": t.status}
        for t in data.tasks if _is_overdue(t.due_date, t.status)
    ]

    workload: dict = {}
    for m in data.team_members:
        tasks = [t for t in data.tasks if t.assignee.lower() == m.name.lower()]
        workload[m.name] = {
            "role": m.role,
            "total": len(tasks),
            "by_status": dict(Counter(t.status for t in tasks)),
        }

    report = {
        "generated_at": _today(),
        "summary": summary,
        "overdue_tasks": overdue,
        "team_workload": workload,
    }
    return json.dumps(report, indent=2)


# ════════════════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    full_report()
