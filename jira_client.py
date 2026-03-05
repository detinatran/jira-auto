"""
jira_client.py — Thin wrapper around the Jira Cloud REST API (v3).

All methods return plain dicts so they can be consumed directly by the
LLM agent, the sync service, or the reporting module.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import requests
from requests.auth import HTTPBasicAuth

import config

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════
#  Low-level HTTP helpers
# ════════════════════════════════════════════════════════════════════════════

def _auth() -> HTTPBasicAuth:
    return HTTPBasicAuth(config.JIRA_EMAIL, config.JIRA_API_TOKEN)


def _headers() -> dict:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _url(path: str) -> str:
    base = config.JIRA_URL.rstrip("/")
    return f"{base}{path}"


def _request(method: str, path: str, **kwargs) -> dict:
    """Generic request wrapper with logging & error handling."""
    resp = requests.request(
        method,
        _url(path),
        auth=_auth(),
        headers=_headers(),
        **kwargs,
    )
    log.debug("%s %s → %s", method, path, resp.status_code)

    if resp.status_code >= 400:
        log.error("Jira error %s: %s", resp.status_code, resp.text)
        return {
            "error": True,
            "status_code": resp.status_code,
            "message": resp.text,
        }

    if resp.status_code == 204:          # No Content
        return {"success": True}

    try:
        return resp.json()
    except json.JSONDecodeError:
        return {"success": True, "raw": resp.text}


# ════════════════════════════════════════════════════════════════════════════
#  Issue CRUD
# ════════════════════════════════════════════════════════════════════════════

# Standard Jira priorities (Cloud default scheme)
PRIORITY_MAP: dict[str, str] = {
    "critical": "Highest",
    "highest":  "Highest",
    "high":     "High",
    "medium":   "Medium",
    "low":      "Low",
    "lowest":   "Lowest",
}

# Map sheet status names → Jira transition / status names
STATUS_MAP: dict[str, str] = {
    "done":        "Resolved",
    "blocked":     "Waiting",
    "in progress": "In Progress",
    "to do":       "To Do",
}


def _normalise_priority(name: str) -> str:
    """Map a free-form priority string to a valid Jira priority name."""
    return PRIORITY_MAP.get(name.strip().lower(), "Medium")


def _normalise_status(name: str) -> str:
    """Map a sheet status to a valid Jira transition/status name."""
    return STATUS_MAP.get(name.strip().lower(), name)


def find_assignable_users(project_key: str, query: str = "") -> list[dict]:
    """Return users who can be assigned to issues in *project_key*."""
    params: dict[str, str] = {"project": project_key}
    if query:
        params["query"] = query
    resp = _request("GET", "/rest/api/3/user/assignable/search", params=params)
    if isinstance(resp, list):
        return resp
    return []


def create_issue(
    project_key: str,
    summary: str,
    description: str = "",
    issue_type: str = "Task",
    assignee_account_id: str | None = None,
    reporter_account_id: str | None = None,
    priority: str = "Medium",
    due_date: str | None = None,
    labels: list[str] | None = None,
) -> dict:
    """
    Create a new Jira issue.  Returns the full Jira response (contains
    ``key``, ``id``, ``self``).
    """
    # Atlassian Document Format (ADF) for description
    desc_adf = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": description or "(no description)"}],
            }
        ],
    }

    fields: dict[str, Any] = {
        "project": {"key": project_key},
        "summary": summary,
        "description": desc_adf,
        "issuetype": {"name": issue_type},
        "priority": {"name": _normalise_priority(priority)},
    }
    if assignee_account_id:
        fields["assignee"] = {"accountId": assignee_account_id}
    if reporter_account_id:
        fields["reporter"] = {"accountId": reporter_account_id}
    if due_date:
        fields["duedate"] = due_date            # YYYY-MM-DD
    if labels:
        fields["labels"] = labels

    return _request("POST", "/rest/api/3/issue", json={"fields": fields})


def get_issue(issue_key: str, fields: str = "*all") -> dict:
    """Fetch a single issue by key (e.g. ``AIM-23``)."""
    return _request("GET", f"/rest/api/3/issue/{issue_key}", params={"fields": fields})


def update_issue(issue_key: str, fields: dict | None = None) -> dict:
    """
    Update fields on an existing issue.

    ``fields`` example: ``{"summary": "New title", "priority": {"name": "High"}}``
    """
    payload: dict[str, Any] = {}
    if fields:
        payload["fields"] = fields
    return _request("PUT", f"/rest/api/3/issue/{issue_key}", json=payload)


def transition_issue(issue_key: str, transition_name: str) -> dict:
    """
    Move an issue to a new status by *transition name*.

    First fetches available transitions, then posts the matching one.
    Tries the normalised name, then the original, then partial matches.
    """
    transitions_resp = _request("GET", f"/rest/api/3/issue/{issue_key}/transitions")
    if "error" in transitions_resp:
        return transitions_resp

    transitions = transitions_resp.get("transitions", [])

    # Build a list of candidate names to try (normalised first, then original)
    normalised = _normalise_status(transition_name)
    candidates = [normalised]
    if transition_name.strip().lower() != normalised.lower():
        candidates.append(transition_name.strip())

    target = None
    for cand in candidates:
        for t in transitions:
            if t["name"].lower() == cand.lower():
                target = t
                break
        if target:
            break
        for t in transitions:
            if t.get("to", {}).get("name", "").lower() == cand.lower():
                target = t
                break
        if target:
            break

    if not target:
        return {
            "error": True,
            "message": f"Transition '{transition_name}' not found. "
                       f"Available: {[t['name'] for t in transitions]}",
        }

    return _request(
        "POST",
        f"/rest/api/3/issue/{issue_key}/transitions",
        json={"transition": {"id": target["id"]}},
    )


def add_comment(issue_key: str, body: str) -> dict:
    """Add a plain-text comment to an issue."""
    adf_body = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": body}],
            }
        ],
    }
    return _request(
        "POST",
        f"/rest/api/3/issue/{issue_key}/comment",
        json={"body": adf_body},
    )


def search_issues(jql: str, fields: list[str] | None = None, max_results: int = 50) -> dict:
    """Run a JQL query and return matching issues."""
    params: dict[str, Any] = {"jql": jql, "maxResults": max_results}
    if fields:
        params["fields"] = ",".join(fields)
    return _request("GET", "/rest/api/3/search", params=params)


def delete_issue(issue_key: str) -> dict:
    """Delete an issue (requires appropriate permissions)."""
    return _request("DELETE", f"/rest/api/3/issue/{issue_key}")


# ════════════════════════════════════════════════════════════════════════════
#  Project helpers
# ════════════════════════════════════════════════════════════════════════════

def list_projects() -> list[dict]:
    """Return a lightweight list of all projects visible to the API user."""
    resp = _request("GET", "/rest/api/3/project")
    if isinstance(resp, list):
        return resp
    return resp.get("values", [resp])


def get_project(key: str) -> dict:
    """Get project details by key."""
    return _request("GET", f"/rest/api/3/project/{key}")


# ════════════════════════════════════════════════════════════════════════════
#  User helpers
# ════════════════════════════════════════════════════════════════════════════

def search_user(query: str) -> list[dict]:
    """Search for a user (display name, email, or accountId prefix)."""
    return _request("GET", "/rest/api/3/user/search", params={"query": query})


# ════════════════════════════════════════════════════════════════════════════
#  Quick test
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if not config.validate_jira_config():
        print("⚠️  Jira credentials not configured. Set them in .env first.")
    else:
        projects = list_projects()
        print(json.dumps(projects, indent=2))
