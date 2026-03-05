"""
sheet_reader.py — Read / write the Google Sheets integration workbook.

Google Sheet URL:
  https://docs.google.com/spreadsheets/d/18parvs_us8AR9GS_ORZUtvGtkFND9qaF/

Uses ``gspread`` + a Google service-account to read and write all tabs.
Provides typed dataclasses and helpers to load every sheet into Python
objects and to write sync results back.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

import config

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════
#  Data models
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class Project:
    project_id: str
    project_name: str
    jira_project_key: str
    owner: str
    start_date: str
    end_date: str
    status: str
    description: str


@dataclass
class Task:
    task_id: str
    project_id: str
    task_name: str
    description: str
    assignee: str
    reporter: str
    priority: str
    status: str
    due_date: str
    jira_issue_key: Optional[str] = None
    sync_status: str = "Pending"
    last_updated: str = ""


@dataclass
class TaskUpdate:
    update_id: str
    task_id: str
    update_type: str
    old_value: Optional[str]
    new_value: Optional[str]
    updated_by: str
    update_time: str


@dataclass
class TeamMember:
    user_id: str
    name: str
    email: str
    jira_account_id: str
    role: str


@dataclass
class SyncLogEntry:
    log_id: str
    task_id: str
    action: str
    jira_issue: Optional[str]
    status: str
    message: str
    timestamp: str


@dataclass
class SheetData:
    """Container holding all sheets in one object."""
    projects: list[Project] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    task_updates: list[TaskUpdate] = field(default_factory=list)
    team_members: list[TeamMember] = field(default_factory=list)
    sync_log: list[SyncLogEntry] = field(default_factory=list)


# ════════════════════════════════════════════════════════════════════════════
#  Google Sheets connection
# ════════════════════════════════════════════════════════════════════════════

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _get_gspread_client() -> gspread.Client:
    """Return an authorised gspread client."""
    creds = Credentials.from_service_account_file(
        config.GOOGLE_SERVICE_ACCOUNT_FILE,
        scopes=_SCOPES,
    )
    return gspread.authorize(creds)


def _open_spreadsheet() -> gspread.Spreadsheet:
    """Open the Google Spreadsheet by its ID."""
    gc = _get_gspread_client()
    return gc.open_by_key(config.GOOGLE_SHEET_ID)


# ════════════════════════════════════════════════════════════════════════════
#  Internal helpers
# ════════════════════════════════════════════════════════════════════════════

def _rows_as_dicts(worksheet) -> list[dict]:
    """Convert a gspread worksheet to list[dict] (header → lower-case)."""
    records = worksheet.get_all_values()
    if len(records) < 2:
        return []
    headers = [h.strip().lower() for h in records[0]]
    return [
        {h: str(cell).strip() for h, cell in zip(headers, row)}
        for row in records[1:]
        if any(cell.strip() for cell in row)
    ]


# ════════════════════════════════════════════════════════════════════════════
#  Load
# ════════════════════════════════════════════════════════════════════════════

def _load_from_google_sheet() -> SheetData:
    """Load all tabs from Google Sheets → SheetData."""
    log.info("Loading data from Google Sheet: %s", config.GOOGLE_SHEET_ID)
    ss = _open_spreadsheet()
    data = SheetData()
    titles = [ws.title for ws in ss.worksheets()]

    if "Projects" in titles:
        for r in _rows_as_dicts(ss.worksheet("Projects")):
            data.projects.append(Project(**r))

    if "Tasks" in titles:
        for r in _rows_as_dicts(ss.worksheet("Tasks")):
            data.tasks.append(Task(**r))

    if "Task Updates" in titles:
        for r in _rows_as_dicts(ss.worksheet("Task Updates")):
            data.task_updates.append(TaskUpdate(**r))

    if "Team Members" in titles:
        for r in _rows_as_dicts(ss.worksheet("Team Members")):
            data.team_members.append(TeamMember(**r))

    if "Sync Log" in titles:
        for r in _rows_as_dicts(ss.worksheet("Sync Log")):
            data.sync_log.append(SyncLogEntry(**r))

    log.info("Loaded %d projects, %d tasks from Google Sheet",
             len(data.projects), len(data.tasks))
    return data


# ════════════════════════════════════════════════════════════════════════════
#  Write helpers
# ════════════════════════════════════════════════════════════════════════════

def _update_task_in_google_sheet(task_id: str, updates: dict) -> None:
    """Update a single task row in the Google Sheet 'Tasks' tab."""
    ss = _open_spreadsheet()
    ws = ss.worksheet("Tasks")
    records = ws.get_all_values()
    if not records:
        return
    headers = [h.strip().lower() for h in records[0]]
    task_id_col = headers.index("task_id")

    for row_idx, row in enumerate(records[1:], start=2):
        if row[task_id_col].strip() == task_id:
            for col_name, new_val in updates.items():
                col_idx = headers.index(col_name.lower())
                ws.update_cell(row_idx, col_idx + 1, new_val)
            log.info("Updated task %s in Google Sheet", task_id)
            return


def _append_sync_log_google(entry: SyncLogEntry) -> None:
    """Append a sync-log row to Google Sheet."""
    ss = _open_spreadsheet()
    ws = ss.worksheet("Sync Log")
    ws.append_row([
        entry.log_id, entry.task_id, entry.action,
        entry.jira_issue or "", entry.status, entry.message,
        entry.timestamp,
    ])


def _append_task_update_google(entry: TaskUpdate) -> None:
    """Append a task-update row to Google Sheet."""
    ss = _open_spreadsheet()
    ws = ss.worksheet("Task Updates")
    ws.append_row([
        entry.update_id, entry.task_id, entry.update_type,
        entry.old_value or "", entry.new_value or "",
        entry.updated_by, entry.update_time,
    ])


# ════════════════════════════════════════════════════════════════════════════
#  Public API
# ════════════════════════════════════════════════════════════════════════════

def load_sheet() -> SheetData:
    """Load all data from Google Sheets."""
    return _load_from_google_sheet()


def get_pending_tasks() -> list[Task]:
    """Return only tasks whose sync_status is 'Pending'."""
    data = load_sheet()
    return [t for t in data.tasks if t.sync_status.lower() == "pending"]


def get_tasks_without_jira_key() -> list[Task]:
    """Return tasks that do NOT have a Jira issue key yet."""
    data = load_sheet()
    return [t for t in data.tasks if not t.jira_issue_key]


def get_project_key_for_task(task: Task, data: SheetData) -> str:
    """Look up the Jira project key for a task's project_id."""
    for p in data.projects:
        if p.project_id == task.project_id:
            return p.jira_project_key
    return ""


def get_team_member(name: str, data: SheetData) -> Optional[TeamMember]:
    """Look up a team member by name."""
    for m in data.team_members:
        if m.name.lower() == name.lower():
            return m
    return None


def update_task_in_sheet(task_id: str, updates: dict) -> None:
    """Update a task row in Google Sheet."""
    _update_task_in_google_sheet(task_id, updates)


def append_sync_log(entry: SyncLogEntry) -> None:
    """Append a sync-log row to Google Sheet."""
    _append_sync_log_google(entry)


def append_task_update(entry: TaskUpdate) -> None:
    """Append a task-update row to Google Sheet."""
    _append_task_update_google(entry)


# ── Quick dump helpers ──────────────────────────────────────────────────────

def tasks_to_dicts(tasks: list[Task]) -> list[dict]:
    """Convert a list of Task dataclasses to plain dicts."""
    return [asdict(t) for t in tasks]


def sheet_summary(data: SheetData | None = None) -> dict:
    """Return a high-level summary dict of the workbook."""
    if data is None:
        data = load_sheet()
    statuses: dict = {}
    for t in data.tasks:
        statuses[t.status] = statuses.get(t.status, 0) + 1
    return {
        "total_projects": len(data.projects),
        "total_tasks": len(data.tasks),
        "tasks_by_status": statuses,
        "pending_sync": sum(1 for t in data.tasks if t.sync_status.lower() == "pending"),
        "team_members": [m.name for m in data.team_members],
    }


if __name__ == "__main__":
    import json
    sd = load_sheet()
    print(json.dumps(sheet_summary(sd), indent=2))
