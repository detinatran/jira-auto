from sheet_reader import load_sheet

data = load_sheet()
for t in data.tasks:
    jira = t.jira_issue_key or "—"
    print(f"  {t.task_id}  sync={t.sync_status:8s}  jira={jira:8s}  {t.task_name}")
