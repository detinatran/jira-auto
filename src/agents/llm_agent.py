"""
llm_agent.py — AI-powered agent that can query and update Jira data.

Supports both OpenAI and Google Gemini with automatic function calling.
Prefers OpenAI if OPENAI_API_KEY is set, falls back to Gemini.
"""

# NOTE: Do NOT use `from __future__ import annotations` here.
# The Gemini SDK inspects function annotations at runtime to build
# the function-calling schema, and it needs real types, not strings.

import json
import logging
import time
from typing import Optional

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    from google import genai
    from google.genai import types
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

from src.utils import config
from src.core import jira_client as jira
from src.core import sheet_reader as sheet
from src.core.sheet_reader import SheetData

log = logging.getLogger(__name__)

MAX_RETRIES = 3

# ════════════════════════════════════════════════════════════════════════════
#  Tool functions exposed to Gemini
# ════════════════════════════════════════════════════════════════════════════
# Each function has a clear docstring + typed params so Gemini can
# auto-generate the JSON schema for function calling.

_sheet_data: Optional[SheetData] = None  # cached per session


def _load_data() -> SheetData:
    global _sheet_data
    if _sheet_data is None:
        _sheet_data = sheet.load_sheet()
    return _sheet_data


def _reload_data() -> SheetData:
    global _sheet_data
    _sheet_data = sheet.load_sheet()
    return _sheet_data


# ── Query / read tools ─────────────────────────────────────────────────────

def _safe_json(func):
    """Decorator: catch exceptions inside tool functions and return JSON error."""
    import functools
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            log.info(f"Tool called: {func.__name__}({kwargs})")
            result = func(*args, **kwargs)
            log.info(f"Tool {func.__name__} returned: {result[:200] if result else 'None'}...")
            return result
        except Exception as exc:
            log.exception(f"Tool {func.__name__} failed")
            return json.dumps({"error": f"{func.__name__} failed: {exc}"})
    # Copy over annotations for Gemini SDK to read parameter types
    wrapper.__annotations__ = func.__annotations__
    return wrapper


@_safe_json
def list_all_projects() -> str:
    """List all projects from the sheet with their key, owner, and status."""
    data = _load_data()
    projects = [
        {
            "project_id": p.project_id,
            "name": p.project_name,
            "jira_key": p.jira_project_key,
            "owner": p.owner,
            "status": p.status,
        }
        for p in data.projects
    ]
    return json.dumps(projects, indent=2)


@_safe_json
def list_tasks(
    project_id: str = "",
    assignee: str = "",
    status: str = "",
    priority: str = "",
) -> str:
    """List tasks with optional filters. Leave a filter empty to skip it.

    Args:
        project_id: Filter by project ID (e.g. PROJ001).
        assignee: Filter by assignee name.
        status: Filter by status (To Do, In Progress, Done, Blocked).
        priority: Filter by priority (Critical, High, Medium, Low).
    """
    data = _load_data()
    tasks = data.tasks

    # Gemini may pass None for unused optional params
    project_id = project_id or ""
    assignee = assignee or ""
    status = status or ""
    priority = priority or ""

    if project_id:
        tasks = [t for t in tasks if (t.project_id or "").lower() == project_id.lower()]
    if assignee:
        tasks = [t for t in tasks if (t.assignee or "").lower() == assignee.lower()]
    if status:
        tasks = [t for t in tasks if (t.status or "").lower() == status.lower()]
    if priority:
        tasks = [t for t in tasks if (t.priority or "").lower() == priority.lower()]

    result = [
        {
            "task_id": t.task_id,
            "project_id": t.project_id,
            "name": t.task_name,
            "assignee": t.assignee,
            "priority": t.priority,
            "status": t.status,
            "due_date": t.due_date,
            "jira_key": t.jira_issue_key or "N/A",
            "sync_status": t.sync_status,
        }
        for t in tasks
    ]
    return json.dumps(result, indent=2)


@_safe_json
def get_task_detail(task_id: str) -> str:
    """Get full details of a single task by its task_id.

    Args:
        task_id: The task ID, e.g. T001.
    """
    data = _load_data()
    task_id = task_id or ""
    for t in data.tasks:
        if (t.task_id or "").upper() == task_id.upper():
            from dataclasses import asdict
            return json.dumps(asdict(t), indent=2)
    return json.dumps({"error": f"Task {task_id} not found"})


@_safe_json
def get_team_members() -> str:
    """List all team members with their roles and emails."""
    data = _load_data()
    members = [
        {
            "name": m.name,
            "email": m.email,
            "role": m.role,
            "jira_account_id": m.jira_account_id,
        }
        for m in data.team_members
    ]
    return json.dumps(members, indent=2)


@_safe_json
def get_project_summary() -> str:
    """Return a summary of all tasks: counts by status, overdue tasks, etc."""
    data = _load_data()
    summary = sheet.sheet_summary(data)
    return json.dumps(summary, indent=2)


@_safe_json
def get_sync_log(task_id: str = "") -> str:
    """Get synchronization log entries, optionally filtered by task_id.

    Args:
        task_id: Filter by task_id. Leave empty to get all logs.
    """
    data = _load_data()
    logs = data.sync_log
    task_id = task_id or ""
    if task_id:
        logs = [l for l in logs if (l.task_id or "").upper() == task_id.upper()]
    result = [
        {
            "log_id": l.log_id,
            "task_id": l.task_id,
            "action": l.action,
            "jira_issue": l.jira_issue,
            "status": l.status,
            "message": l.message,
            "timestamp": l.timestamp,
        }
        for l in logs
    ]
    return json.dumps(result, indent=2)


@_safe_json
def search_jira_issues(jql: str) -> str:
    """Search Jira issues using a JQL query.

    Args:
        jql: The JQL query string, e.g. 'project=AIM AND status="In Progress"'.
    """
    if not config.validate_jira_config():
        return json.dumps({"error": "Jira credentials not configured. Cannot query Jira directly. Using local sheet data instead."})
    resp = jira.search_issues(jql, fields=["summary", "status", "assignee", "priority", "duedate"])
    return json.dumps(resp, indent=2, default=str)


# ── Write / mutation tools ─────────────────────────────────────────────────

@_safe_json
def create_jira_issue(
    project_key: str,
    summary: str,
    description: str = "",
    assignee_name: str = "",
    priority: str = "Medium",
    due_date: str = "",
) -> str:
    """Create a new Jira issue.

    Args:
        project_key: Jira project key (e.g. KAN, MJLP) or project ID (e.g. PROJ001).
        summary: Brief title of the issue.
        description: Detailed description.
        assignee_name: Name of the assignee (must match team member name).
        priority: Priority level (Critical, High, Medium, Low).
        due_date: Due date in YYYY-MM-DD format.
    """
    if not config.validate_jira_config():
        return json.dumps({
            "status": "dry_run",
            "message": f"Would create issue '{summary}' in {project_key}. "
                       "Jira credentials not configured.",
        })

    data = _load_data()

    # Auto-resolve: if user passes project_id (e.g. PROJ001), find the Jira key
    resolved_key = project_key
    for p in data.projects:
        if (p.project_id or "").upper() == project_key.upper():
            resolved_key = p.jira_project_key
            log.info(f"Resolved project_id {project_key} → Jira key {resolved_key}")
            break
        if (p.project_name or "").lower() == project_key.lower():
            resolved_key = p.jira_project_key
            log.info(f"Resolved project_name '{project_key}' → Jira key {resolved_key}")
            break

    account_id = None
    if assignee_name:
        member = sheet.get_team_member(assignee_name, data)
        if member:
            account_id = member.jira_account_id

    resp = jira.create_issue(
        project_key=resolved_key,
        summary=summary,
        description=description,
        assignee_account_id=account_id,
        priority=priority,
        due_date=due_date or None,
    )
    _reload_data()
    return json.dumps(resp, indent=2, default=str)


@_safe_json
def update_jira_issue(
    issue_key: str,
    summary: str = "",
    priority: str = "",
    status: str = "",
    assignee_name: str = "",
    due_date: str = "",
    comment: str = "",
) -> str:
    """Update an existing Jira issue.

    Args:
        issue_key: Jira issue key (e.g. KAN-13).
        summary: New summary / title (leave empty to keep current).
        priority: New priority (leave empty to keep current).
        status: Transition to this status (e.g. In Progress, Done).
        assignee_name: New assignee name (must match team member name).
        due_date: New due date in YYYY-MM-DD format.
        comment: Add a comment to the issue.
    """
    if not config.validate_jira_config():
        return json.dumps({
            "status": "dry_run",
            "message": f"Would update {issue_key}. Jira credentials not configured.",
        })

    data = _load_data()
    results = {}

    # Update fields
    fields = {}
    if summary:
        fields["summary"] = summary
    if priority:
        fields["priority"] = {"name": priority}
    if assignee_name:
        member = sheet.get_team_member(assignee_name, data)
        if member:
            fields["assignee"] = {"accountId": member.jira_account_id}
    if due_date:
        fields["duedate"] = due_date
    
    if fields:
        results["field_update"] = jira.update_issue(issue_key, fields=fields)

    # Transition
    if status:
        results["transition"] = jira.transition_issue(issue_key, status)

    # Comment
    if comment:
        results["comment"] = jira.add_comment(issue_key, comment)

    _reload_data()
    return json.dumps(results, indent=2, default=str)


@_safe_json
def get_jira_issue_detail(issue_key: str) -> str:
    """Fetch full details of a Jira issue from the Jira API.

    Args:
        issue_key: Jira issue key, e.g. AIM-23.
    """
    if not config.validate_jira_config():
        return json.dumps({"error": "Jira credentials not configured."})
    resp = jira.get_issue(issue_key)
    # Slim down the response for the LLM
    if "fields" in resp:
        f = resp["fields"]
        return json.dumps({
            "key": resp.get("key"),
            "summary": f.get("summary"),
            "status": f.get("status", {}).get("name"),
            "priority": f.get("priority", {}).get("name"),
            "assignee": (f.get("assignee") or {}).get("displayName"),
            "reporter": (f.get("reporter") or {}).get("displayName"),
            "duedate": f.get("duedate"),
            "created": f.get("created"),
            "updated": f.get("updated"),
        }, indent=2, default=str)
    return json.dumps(resp, indent=2, default=str)


# ════════════════════════════════════════════════════════════════════════════
#  The tool registry (list of Python functions for Gemini)
# ════════════════════════════════════════════════════════════════════════════

TOOLS = [
    list_all_projects,
    list_tasks,
    get_task_detail,
    get_team_members,
    get_project_summary,
    get_sync_log,
    search_jira_issues,
    create_jira_issue,
    update_jira_issue,
    get_jira_issue_detail,
]

# ════════════════════════════════════════════════════════════════════════════
#  System prompt
# ════════════════════════════════════════════════════════════════════════════

SYSTEM_INSTRUCTION = """\
You are **Jira Assistant**, an AI agent that helps manage project tasks.

You have access to tools that can:
• Read project and task data from the local spreadsheet
• Query Jira directly via JQL
• Create and update Jira issues
• Look up team members and sync logs

Guidelines:
1. Always use the available tools to answer questions — do NOT guess data.
2. When asked about tasks, projects, or team members, call the appropriate
   list/query function first.
3. Present information in a clear, concise, tabular format when possible.
4. If Jira credentials are not configured, explain that you are using local
   sheet data and that write operations will be simulated (dry-run).
5. For create/update operations, execute them directly without asking
   for confirmation (the user already told you what to do).
6. Always mention the task_id and jira_issue_key when referring to a task.
7. When creating issues, you can pass either the Jira project key (e.g. KAN, MJLP)
   or the project ID (e.g. PROJ001) — the system will auto-resolve it.
8. If a tool returns an error JSON, tell the user what went wrong specifically.
"""

# ════════════════════════════════════════════════════════════════════════════
#  Agent class
# ════════════════════════════════════════════════════════════════════════════

class JiraAgent:
    """Interactive chat agent backed by OpenAI or Gemini with function calling."""

    def __init__(self, model: Optional[str] = None, provider: Optional[str] = None):
        """Initialize agent with OpenAI (preferred) or Gemini.
        
        Args:
            model: Model name (auto-selected if None)
            provider: 'openai' or 'gemini' (auto-detected if None)
        """
        # Auto-detect provider
        if provider is None:
            if config.OPENAI_API_KEY and HAS_OPENAI:
                provider = 'openai'
            elif config.GOOGLE_API_KEY and HAS_GEMINI:
                provider = 'gemini'
            else:
                raise RuntimeError(
                    "No AI API key configured. "
                    "Please set OPENAI_API_KEY or GOOGLE_API_KEY in .env"
                )
        
        self._provider = provider
        
        if provider == 'openai':
            if not HAS_OPENAI:
                raise RuntimeError("openai package not installed. Run: pip install openai")
            if not config.OPENAI_API_KEY:
                raise RuntimeError("OPENAI_API_KEY not set in .env")
            
            self._model = model or "gpt-4o-mini"
            self._client = OpenAI(api_key=config.OPENAI_API_KEY)
            self._messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
            log.info(f"Initialized OpenAI agent with model: {self._model}")
            
        elif provider == 'gemini':
            if not HAS_GEMINI:
                raise RuntimeError("google-genai package not installed")
            if not config.GOOGLE_API_KEY:
                raise RuntimeError("GOOGLE_API_KEY not set in .env")
            
            self._model = model or "gemini-2.5-flash"
            self._client = genai.Client(api_key=config.GOOGLE_API_KEY)
            self._chat = self._client.chats.create(
                model=self._model,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    tools=TOOLS,
                    temperature=0.2,
                ),
            )
            log.info(f"Initialized Gemini agent with model: {self._model}")
        else:
            raise ValueError(f"Unknown provider: {provider}")
        
        # Pre-load sheet data
        _load_data()

    def send(self, message: str) -> str:
        """Send a user message and return the assistant's text reply.
        
        Supports automatic function calling for both OpenAI and Gemini.
        """
        if self._provider == 'openai':
            return self._send_openai(message)
        else:
            return self._send_gemini(message)
    
    def _send_openai(self, message: str) -> str:
        """Send message using OpenAI with function calling."""
        self._messages.append({"role": "user", "content": message})
        
        # Convert TOOLS to OpenAI format
        tools_openai = []
        for func in TOOLS:
            import inspect
            sig = inspect.signature(func)
            params = {}
            required = []
            
            for name, param in sig.parameters.items():
                param_type = "string"  # default
                if param.annotation == int:
                    param_type = "integer"
                elif param.annotation == float:
                    param_type = "number"
                elif param.annotation == bool:
                    param_type = "boolean"
                
                params[name] = {"type": param_type}
                if param.default == inspect.Parameter.empty:
                    required.append(name)
            
            tools_openai.append({
                "type": "function",
                "function": {
                    "name": func.__name__,
                    "description": (func.__doc__ or "").split("\n\n")[0].strip(),
                    "parameters": {
                        "type": "object",
                        "properties": params,
                        "required": required,
                    }
                }
            })
        
        # Call OpenAI with tools
        response = self._client.chat.completions.create(
            model=self._model,
            messages=self._messages,
            tools=tools_openai,
            temperature=0.2,
        )
        
        message_obj = response.choices[0].message
        
        # Handle function calls
        if message_obj.tool_calls:
            self._messages.append(message_obj)
            
            for tool_call in message_obj.tool_calls:
                func_name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)
                
                # Find and call the function
                func = next((f for f in TOOLS if f.__name__ == func_name), None)
                if func:
                    log.info(f"OpenAI calling tool: {func_name}({args})")
                    result = func(**args)
                    self._messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    })
            
            # Get final response after function calls
            response = self._client.chat.completions.create(
                model=self._model,
                messages=self._messages,
                temperature=0.2,
            )
            message_obj = response.choices[0].message
        
        self._messages.append({"role": "assistant", "content": message_obj.content})
        return message_obj.content or "(no response)"
    
    def _send_gemini(self, message: str) -> str:
        """Send message using Gemini with function calling (includes retry)."""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._chat.send_message(message)
                return response.text or "(no text response)"
            except Exception as exc:
                err_str = str(exc)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    # Parse retry delay from error message if available
                    wait = 10 * attempt  # default backoff
                    import re
                    match = re.search(r'retryDelay.*?(\d+)', err_str)
                    if match:
                        wait = int(match.group(1)) + 2
                    if attempt < MAX_RETRIES:
                        log.info(f"Rate limited. Waiting {wait}s before retry {attempt}/{MAX_RETRIES}...")
                        print(f"⏳ Rate limited. Waiting {wait}s before retry ({attempt}/{MAX_RETRIES})...")
                        time.sleep(wait)
                        continue
                raise

    def reset(self):
        """Start a fresh conversation."""
        if self._provider == 'openai':
            self._messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
        else:
            self._chat = self._client.chats.create(
                model=self._model,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    tools=TOOLS,
                    temperature=0.2,
                ),
            )
        _reload_data()


# ════════════════════════════════════════════════════════════════════════════
#  Standalone interactive REPL
# ════════════════════════════════════════════════════════════════════════════

def interactive_chat():
    """Launch a terminal-based chat loop."""
    from rich.console import Console
    from rich.markdown import Markdown

    console = Console()
    
    agent = JiraAgent()
    provider_name = "OpenAI" if agent._provider == "openai" else "Gemini"
    console.print(f"[bold cyan]🤖 Jira Assistant[/] ({provider_name} + Function Calling)")
    console.print(f"Model: [dim]{agent._model}[/]")
    console.print("Type [bold]quit[/] or [bold]exit[/] to leave.\n")

    while True:
        try:
            user_input = console.input("[bold green]You:[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            console.print("[dim]Goodbye![/]")
            break
        if user_input.lower() == "/reset":
            agent.reset()
            console.print("[dim]Conversation reset.[/]\n")
            continue

        try:
            reply = agent.send(user_input)
            console.print()
            console.print(Markdown(reply))
            console.print()
        except Exception as exc:
            log.exception("Agent send failed")
            console.print(f"[bold red]Error:[/] {exc}\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    interactive_chat()
