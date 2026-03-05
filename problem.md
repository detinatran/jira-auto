# Automated Task Synchronization Between Sheet and Jira with LLM Integration

## 1. Problem Description

The goal of this system is to build an **automated workflow** that synchronizes project tasks between a spreadsheet and Jira, while also enabling an **LLM agent to query and update Jira data**.

The workflow works as follows:

- A **Google Sheet or Excel Sheet** is used as the main interface to input **tasks or projects**.
- Whenever a user **adds or updates a task in the sheet**, the system will automatically:
  - Create or update the corresponding **issue in Jira** (a project management tool).
- At the same time:
  - Data from Jira will be **collected for reporting or analytics purposes**.
  - An **LLM agent** will be able to:
    - Query Jira data (e.g., tasks, status, assignees, priorities)
    - Update Jira data (create tasks, update status, add comments)

The objective is to build a **fully automated pipeline connecting Sheet → Jira → Data analytics + LLM agent**.

---

## 2. High-Level Architecture

The system architecture can be summarized as follows:

```
Google Sheet / Excel
│
│ (Webhook / Script / API)
▼
Integration Service (Python / Node.js)
│
│ Jira REST API
▼
Jira
│
├── Data collection for reporting
└── LLM Agent (RAG / MCP tools) for querying & updating data
```

---

## 3. Example Workflow

### Step 1 — User Inputs Tasks in Sheet

Example table in the sheet:

| Task Name | Description | Assignee | Priority |
|-----------|-------------|----------|----------|
| Build API | Create endpoint | duong | High |

---

### Step 2 — Script Reads Sheet and Sends Data to Jira

Example Python script:

```python
import requests

JIRA_URL = "https://your-domain.atlassian.net"
API_TOKEN = "your_token"
EMAIL = "your_email"

url = f"{JIRA_URL}/rest/api/3/issue"

payload = {
    "fields": {
        "project": {
            "key": "PROJ"
        },
        "summary": "Build API",
        "description": "Create endpoint",
        "issuetype": {
            "name": "Task"
        },
        "assignee": {
            "name": "duong"
        },
        "priority": {
            "name": "High"
        }
    }
}

response = requests.post(
    url,
    json=payload,
    auth=(EMAIL, API_TOKEN),
    headers={"Content-Type": "application/json"}
)

print(response.json())
```

When this script runs, the task will be **automatically created in Jira**.

---

## 4. JSON Format for LLM Querying Jira

The LLM requires a **standard JSON schema** to interact with the Jira backend tools.

### Query Issues

```json
{
  "action": "query_issue",
  "filters": {
    "project": "PROJ",
    "status": "In Progress",
    "assignee": "duong"
  },
  "fields": ["summary", "status", "assignee", "priority"]
}
```

---

### Create Issue

```json
{
  "action": "create_issue",
  "data": {
    "project": "PROJ",
    "summary": "Build API",
    "description": "Create endpoint for task service",
    "assignee": "duong",
    "priority": "High",
    "type": "Task"
  }
}
```

---

### Update Issue

```json
{
  "action": "update_issue",
  "issue_key": "PROJ-123",
  "updates": {
    "status": "Done",
    "comment": "Task completed"
  }
}
```

---

## 5. LLM Tool Configuration (Agent / MCP)

Example configuration for a tool that allows the LLM to interact with Jira:

```json
{
  "name": "jira_tool",
  "description": "Tool to query or update Jira issues",
  "actions": [
    "query_issue",
    "create_issue",
    "update_issue"
  ],
  "endpoint": "https://your-backend.com/jira",
  "method": "POST"
}
```

Workflow:

1. The **LLM generates a JSON request**
2. The **backend parses the JSON**
3. The backend **calls the Jira REST API**

---

## 6. Collecting Data from Jira for Reporting

Jira data can be retrieved using the **Jira REST API** and stored in a data pipeline.

Example query:

```
GET /rest/api/3/search?jql=project=PROJ
```

The retrieved data can be stored in:

* PostgreSQL
* BigQuery
* Snowflake

This data can then be used to build dashboards using tools such as:

* Metabase
* Apache Superset
* Power BI

---

## 7. Problem Summary

This problem involves building an integration system where users input tasks or projects into a spreadsheet, and the system automatically synchronizes those tasks with Jira through the Jira REST API. Additionally, the system collects Jira data for reporting and analytics purposes. An LLM agent is integrated into the architecture to generate structured JSON commands that allow it to query or update Jira issues, enabling automated task management and flexible information retrieval.
