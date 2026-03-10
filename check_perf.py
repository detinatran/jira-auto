#!/usr/bin/env python3
"""
check_perf.py — Validate the full architecture without live API calls.

Checks:
  1. Module imports
  2. Config values loaded
  3. MCP CLI tools reachable
  4. Jira REST connectivity (if credentials are set)
  5. Google Sheet connectivity (if service-account file exists)
  6. LLM provider reachable
  7. MCP StdioServerParameters build
  8. ReAct action parser correctness
  9. Pipeline JSON-array parser correctness
"""

from __future__ import annotations

import shutil
import sys
import time

OK   = "  [OK]   "
ERR  = "  [ERR]  "
SKIP = "  [SKIP] "

results: list[tuple[str, str]] = []


def record(status: str, label: str, detail: str = "") -> None:
    tag = f"{label}: {detail}" if detail else label
    results.append((status, tag))
    print(f"{status} {tag}")


# ════════════════════════════════════════════════════════════════════════
# 1. Module imports
# ════════════════════════════════════════════════════════════════════════
print("\n── 1. Module imports ──")
_modules = {
    "config":       "src.utils.config",
    "jira_client":  "src.core.jira_client",
    "sheet_reader": "src.core.sheet_reader",
    "sync_service": "src.core.sync_service",
    "llm_agent":    "src.agents.llm_agent",
    "mcp.client":   "src.mcp.client",
    "mcp_agent":    "src.agents.mcp_agent",
    "pipeline":     "src.core.pipeline",
    "react_agent":  "src.agents.react_agent",
}
for label, mod in _modules.items():
    t0 = time.perf_counter()
    try:
        __import__(mod)
        ms = (time.perf_counter() - t0) * 1000
        record(OK, label, f"{ms:.0f} ms")
    except Exception as exc:
        record(ERR, label, str(exc))

# ════════════════════════════════════════════════════════════════════════
# 2. Config values
# ════════════════════════════════════════════════════════════════════════
print("\n── 2. Config values ──")
from src.utils import config


def _mask(s: str) -> str:
    return (s[:4] + "…" + s[-4:]) if len(s) > 10 else ("(set)" if s else "(missing)")


for key, val in [
    ("JIRA_URL",            config.JIRA_URL),
    ("JIRA_EMAIL",          config.JIRA_EMAIL),
    ("JIRA_API_TOKEN",      config.JIRA_API_TOKEN),
    ("GOOGLE_SHEET_ID",     config.GOOGLE_SHEET_ID),
    ("GOOGLE_SERVICE_ACCOUNT_FILE", config.GOOGLE_SERVICE_ACCOUNT_FILE),
    ("OPENAI_API_KEY",      config.OPENAI_API_KEY),
    ("GOOGLE_API_KEY",      config.GOOGLE_API_KEY),
    ("MCP_SHEET_COMMAND",   config.MCP_SHEET_COMMAND),
    ("MCP_JIRA_COMMAND",    config.MCP_JIRA_COMMAND),
]:
    status = OK if val and val != "https://your-domain.atlassian.net" else SKIP
    record(status, key, _mask(val))

# ════════════════════════════════════════════════════════════════════════
# 3. MCP CLI commands reachable
# ════════════════════════════════════════════════════════════════════════
print("\n── 3. MCP CLI commands ──")
for cmd in [config.MCP_SHEET_COMMAND, config.MCP_JIRA_COMMAND]:
    path = shutil.which(cmd)
    if path:
        record(OK, cmd, path)
    else:
        record(ERR, cmd, "not found on PATH")

# ════════════════════════════════════════════════════════════════════════
# 4. Jira REST connectivity
# ════════════════════════════════════════════════════════════════════════
print("\n── 4. Jira REST connectivity ──")
if not config.validate_jira_config():
    record(SKIP, "Jira API", "credentials missing in .env")
else:
    try:
        import requests
        from requests.auth import HTTPBasicAuth
        t0 = time.perf_counter()
        resp = requests.get(
            f"{config.JIRA_URL.rstrip('/')}/rest/api/3/myself",
            auth=HTTPBasicAuth(config.JIRA_EMAIL, config.JIRA_API_TOKEN),
            headers={"Accept": "application/json"},
            timeout=10,
        )
        ms = (time.perf_counter() - t0) * 1000
        if resp.status_code == 200:
            name = resp.json().get("displayName", "?")
            record(OK, "Jira API", f"HTTP {resp.status_code} – logged in as '{name}' ({ms:.0f} ms)")
        else:
            record(ERR, "Jira API", f"HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as exc:
        record(ERR, "Jira API", str(exc))

# ════════════════════════════════════════════════════════════════════════
# 5. Google Sheet connectivity
# ════════════════════════════════════════════════════════════════════════
print("\n── 5. Google Sheet connectivity ──")
if not config.validate_google_sheet_config():
    record(SKIP, "Google Sheet", "service-account file missing")
else:
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        t0 = time.perf_counter()
        creds = Credentials.from_service_account_file(
            config.GOOGLE_SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
        )
        gc = gspread.authorize(creds)
        ss = gc.open_by_key(config.GOOGLE_SHEET_ID)
        ms = (time.perf_counter() - t0) * 1000
        tabs = [ws.title for ws in ss.worksheets()]
        record(OK, "Google Sheet", f"'{ss.title}' | tabs: {tabs} ({ms:.0f} ms)")
    except Exception as exc:
        record(ERR, "Google Sheet", str(exc))

# ════════════════════════════════════════════════════════════════════════
# 6. LLM provider reachable
# ════════════════════════════════════════════════════════════════════════
print("\n── 6. LLM provider ──")
if config.OPENAI_API_KEY:
    try:
        from openai import OpenAI
        t0 = time.perf_counter()
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        models = list(client.models.list())
        ms = (time.perf_counter() - t0) * 1000
        record(OK, "OpenAI", f"{len(models)} models available ({ms:.0f} ms)")
    except Exception as exc:
        record(ERR, "OpenAI", str(exc))
elif config.GOOGLE_API_KEY:
    try:
        from google import genai
        t0 = time.perf_counter()
        c = genai.Client(api_key=config.GOOGLE_API_KEY)
        mdls = list(c.models.list())
        ms = (time.perf_counter() - t0) * 1000
        record(OK, "Gemini", f"{len(mdls)} models available ({ms:.0f} ms)")
    except Exception as exc:
        record(ERR, "Gemini", str(exc))
else:
    record(SKIP, "LLM provider", "no API key set")

# ════════════════════════════════════════════════════════════════════════
# 7. MCP StdioServerParameters build
# ════════════════════════════════════════════════════════════════════════
print("\n── 7. MCP client config ──")
try:
    from src.mcp.client import _build_server_params
    from mcp.client.stdio import StdioServerParameters
    p = _build_server_params(command="echo", args=["hello"])
    assert isinstance(p, StdioServerParameters), f"expected StdioServerParameters, got {type(p)}"
    assert p.command == "echo"
    record(OK, "StdioServerParameters", f"command={p.command} args={p.args}")
except Exception as exc:
    record(ERR, "StdioServerParameters", str(exc))

# ════════════════════════════════════════════════════════════════════════
# 8. ReAct action parser
# ════════════════════════════════════════════════════════════════════════
print("\n── 8. ReAct action parser ──")
try:
    from src.agents.react_agent import ReactAgent
    cases = [
        (
            'Thought: I need sheet data.\nAction: sheet_get_sheet_data\nAction Input: {"spreadsheet_id":"abc","range":"Tasks"}',
            "sheet_get_sheet_data",
            {"spreadsheet_id": "abc", "range": "Tasks"},
        ),
        (
            "Thought: Done.\nFinal Answer: Created 3 issues.",
            None,
            {},
        ),
    ]
    for text, exp_action, exp_input in cases:
        action, ai = ReactAgent._parse_action(text)
        assert action == exp_action, f"action: got {action!r}, want {exp_action!r}"
        assert ai    == exp_input,   f"input:  got {ai!r}, want {exp_input!r}"
    record(OK, "ReAct parser", f"{len(cases)} cases passed")
except Exception as exc:
    record(ERR, "ReAct parser", str(exc))

# ════════════════════════════════════════════════════════════════════════
# 9. Pipeline JSON-array parser
# ════════════════════════════════════════════════════════════════════════
print("\n── 9. Pipeline JSON parser ──")
try:
    from src.core.pipeline import SyncPipeline
    cases = [
        ('[{"summary":"Fix bug","priority":"High"}]',               1),
        ('```json\n[{"summary":"Task 1"},{"summary":"Task 2"}]\n```', 2),
        ('Some preamble\n[{"summary":"x"}]',                        1),
    ]
    for text, expected in cases:
        parsed = SyncPipeline._parse_json_array(text)
        assert len(parsed) == expected, f"got {len(parsed)}, want {expected}"
    record(OK, "Pipeline parser", f"{len(cases)} cases passed")
except Exception as exc:
    record(ERR, "Pipeline parser", str(exc))

# ════════════════════════════════════════════════════════════════════════
# Summary
# ════════════════════════════════════════════════════════════════════════
print("\n" + "─" * 55)
ok_count   = sum(1 for s, _ in results if s == OK)
err_count  = sum(1 for s, _ in results if s == ERR)
skip_count = sum(1 for s, _ in results if s == SKIP)
print(f"  OK: {ok_count}   ERR: {err_count}   SKIP: {skip_count}")
if err_count:
    print("\nFailed checks:")
    for s, label in results:
        if s == ERR:
            print(f"    {label}")
    sys.exit(1)
else:
    print("\n  All checks passed. SKIPs = missing credentials (expected).")
