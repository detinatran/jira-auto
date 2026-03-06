"""
Fix the Google Sheet to use real Jira project keys.

Your Jira instance has:
  KAN   - ad's corner
  MJLP  - Healthcare CRM

Mapping:
  AIM (AI Task Manager)     → KAN
  MAR (Mobile App Redesign) → KAN
  DBM (Database Migration)  → MJLP
  CP  (Customer Portal)     → MJLP
"""
import gspread
from google.oauth2.service_account import Credentials
from src.utils import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_file(
    config.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
gc = gspread.authorize(creds)
ss = gc.open_by_key(config.GOOGLE_SHEET_ID)

# ── 1. Update project keys in the Projects sheet ──
KEY_MAP = {"AIM": "KAN", "MAR": "KAN", "DBM": "MJLP", "CP": "MJLP"}

proj_ws = ss.worksheet("Projects")
proj_data = proj_ws.get_all_records()
for i, row in enumerate(proj_data, start=2):  # row 1 = header
    old_key = row.get("jira_project_key", "")
    if old_key in KEY_MAP:
        new_key = KEY_MAP[old_key]
        proj_ws.update_cell(i, list(proj_data[0].keys()).index("jira_project_key") + 1 if False else 3, new_key)
        print(f"  Projects row {i}: {old_key} → {new_key}")

# Find the correct column index for jira_project_key
proj_header = proj_ws.row_values(1)
jira_key_col = proj_header.index("jira_project_key") + 1  # 1-based
print(f"\nProjects jira_project_key is column {jira_key_col}")

for i, row in enumerate(proj_data, start=2):
    old_key = row.get("jira_project_key", "")
    if old_key in KEY_MAP:
        proj_ws.update_cell(i, jira_key_col, KEY_MAP[old_key])
        print(f"  Projects row {i}: {old_key} → {KEY_MAP[old_key]}")

# ── 2. Fix failed tasks: reset to Pending, clear invalid Jira keys ──
task_ws = ss.worksheet("Tasks")
task_header = task_ws.row_values(1)
task_data = task_ws.get_all_records()

sync_col = task_header.index("sync_status") + 1
jira_col = task_header.index("jira_issue_key") + 1

# Tasks that failed and had fake Jira keys (not real issues)
# Clear jira_issue_key for T004, T014 (AIM-26, MAR-16 don't exist)
# T009, T010, T015 already have no key
CLEAR_KEYS = {"T004", "T014"}  # had fake keys that don't exist in Jira
RESET_TASKS = {"T004", "T009", "T010", "T014", "T015"}

for i, row in enumerate(task_data, start=2):
    tid = row.get("task_id", "")
    if tid in RESET_TASKS:
        task_ws.update_cell(i, sync_col, "Pending")
        print(f"  {tid}: sync_status → Pending")
    if tid in CLEAR_KEYS:
        task_ws.update_cell(i, jira_col, "")
        print(f"  {tid}: cleared invalid jira_issue_key")

# Also reset all "Synced" tasks with old fake keys to Pending
# so they get created in the real projects
OLD_FAKE_KEYS = {"AIM-23", "AIM-24", "AIM-25", "AIM-27",
                 "MAR-12", "MAR-13", "MAR-14", "MAR-15",
                 "CP-45", "CP-46"}

for i, row in enumerate(task_data, start=2):
    tid = row.get("task_id", "")
    jk = row.get("jira_issue_key", "")
    if jk in OLD_FAKE_KEYS:
        task_ws.update_cell(i, jira_col, "")
        task_ws.update_cell(i, sync_col, "Pending")
        print(f"  {tid}: cleared fake key {jk}, reset to Pending")

print("\n✅ Google Sheet updated. All tasks are now Pending with real project keys.")
