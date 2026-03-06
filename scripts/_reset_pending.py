"""Reset all Failed tasks back to Pending for re-sync."""
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

task_ws = ss.worksheet("Tasks")
header = task_ws.row_values(1)
sync_col = header.index("sync_status") + 1

data = task_ws.get_all_records()
for i, row in enumerate(data, start=2):
    cur = row.get("sync_status", "").strip()
    if cur.lower() in ("failed", "synced"):
        task_ws.update_cell(i, sync_col, "Pending")
        print(f"  {row['task_id']}: {cur} → Pending")

print("Done.")
