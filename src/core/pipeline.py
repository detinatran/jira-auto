"""
Step 2 — Backend Pipeline  (Sheet → LLM → Jira)

A structured, deterministic pipeline:
  1. Read sheet data  via  Google Sheets MCP server
  2. Send data to LLM for field mapping / transformation
  3. Create Jira issues  via  Atlassian MCP server

The LLM is used **only** for text transformation — it does NOT decide
which tools to call.  The pipeline itself orchestrates the flow.

Architecture::

    Sheet MCP  ──read──→  raw rows
                             ↓
                         LLM (transform / map fields)
                             ↓
    Jira MCP   ←─create─  issue payloads
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from src.utils import config
from src.mcp.client import MCPClientManager

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
#  Transformation prompt
# ════════════════════════════════════════════════════════════════════════════

TRANSFORM_PROMPT = """\
You are a data-transformation engine.
Convert the following Google Sheet rows into Jira issue payloads.

**Sheet data** (JSON):
```
{sheet_data}
```

**Target Jira project key:** `{project_key}`

For **each row**, produce a JSON object with exactly these fields:
  - summary      (string)  — brief issue title
  - description  (string)  — detailed description
  - priority     (string)  — one of: Highest, High, Medium, Low, Lowest
  - assignee     (string)  — display name if present, else ""
  - due_date     (string)  — YYYY-MM-DD if present, else ""
  - issue_type   (string)  — Task, Bug, Story, etc.  Default "Task"

**Rules:**
1. Map sheet column names intelligently (e.g. "Task Name" → summary).
2. If a column is missing or empty, use sensible defaults.
3. Skip completely empty rows.
4. Return **only** a valid JSON array — no markdown, no explanation.

Output:
[
  {{"summary":"…","description":"…","priority":"Medium","assignee":"","due_date":"","issue_type":"Task"}},
  …
]
"""


# ════════════════════════════════════════════════════════════════════════════
#  Pipeline class
# ════════════════════════════════════════════════════════════════════════════

class SyncPipeline:
    """Backend pipeline:  Sheet MCP  →  LLM transform  →  Jira MCP."""

    def __init__(self, mcp: MCPClientManager):
        self._mcp = mcp

        # Pick LLM provider
        if config.OPENAI_API_KEY:
            from openai import OpenAI
            self._provider = "openai"
            self._client = OpenAI(api_key=config.OPENAI_API_KEY)
            self._model = "gpt-4o-mini"
        elif config.GOOGLE_API_KEY:
            from google import genai
            self._provider = "gemini"
            self._client = genai.Client(api_key=config.GOOGLE_API_KEY)
            self._model = "gemini-2.5-flash"
        else:
            raise RuntimeError(
                "No AI API key configured. "
                "Set OPENAI_API_KEY or GOOGLE_API_KEY in .env"
            )

    # ── public API ──────────────────────────────────────────────────────

    async def run(
        self,
        spreadsheet_id: str = "",
        sheet_range: str = "",
        project_key: str = "",
    ) -> list[dict]:
        """Execute the full pipeline and return per-row results."""
        spreadsheet_id = spreadsheet_id or config.GOOGLE_SHEET_ID
        sheet_range = sheet_range or "Tasks"

        # ── Step 1: Read Google Sheet via MCP ──────────────────────────
        log.info("Pipeline [1/3]: reading sheet data…")
        sheet_data = await self._read_sheet(spreadsheet_id, sheet_range)
        if not sheet_data:
            return [{"error": "No data returned from sheet"}]
        row_count = len(sheet_data) if isinstance(sheet_data, list) else "?"
        log.info("Pipeline [1/3]: got %s rows", row_count)

        # ── Step 2: LLM transforms rows → issue payloads ──────────────
        log.info("Pipeline [2/3]: LLM transforming data…")
        issues = self._transform(sheet_data, project_key)
        if not issues:
            return [{"error": "LLM produced no issue payloads"}]
        log.info("Pipeline [2/3]: LLM produced %d payloads", len(issues))

        # ── Step 3: Create Jira issues via MCP ─────────────────────────
        log.info("Pipeline [3/3]: creating Jira issues…")
        results: list[dict] = []
        for idx, issue in enumerate(issues, 1):
            res = await self._create_jira_issue(project_key, issue)
            results.append({"row": idx, "summary": issue.get("summary", ""), **res})
            status = "[OK]" if res.get("success") else "[FAIL]"
            log.info("  %s [%d/%d] %s", status, idx, len(issues), res.get("message"))

        return results

    # ── internals ───────────────────────────────────────────────────────

    async def _read_sheet(self, spreadsheet_id: str, sheet_range: str) -> Any:
        """Call the Sheet MCP server to fetch spreadsheet data."""
        raw = await self._mcp.call_sheet_tool("get_sheet_data", {
            "spreadsheet_id": spreadsheet_id,
            "range": sheet_range,
        })
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw

    def _transform(self, sheet_data: Any, project_key: str) -> list[dict]:
        """Ask the LLM to convert sheet rows into Jira issue payloads."""
        data_str = (json.dumps(sheet_data, indent=2)
                    if isinstance(sheet_data, (list, dict)) else str(sheet_data))
        prompt = TRANSFORM_PROMPT.format(
            sheet_data=data_str,
            project_key=project_key,
        )

        if self._provider == "openai":
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            text = resp.choices[0].message.content or ""
        else:
            resp = self._client.models.generate_content(
                model=self._model, contents=prompt,
            )
            text = resp.text or ""

        return self._parse_json_array(text)

    async def _create_jira_issue(self, project_key: str, issue: dict) -> dict:
        """Call the Jira MCP server to create one issue."""
        try:
            raw = await self._mcp.call_tool("jira_create_issue", {
                "project_key": project_key,
                "summary": issue.get("summary", "Untitled"),
                "description": issue.get("description", ""),
                "issue_type": issue.get("issue_type", "Task"),
                "priority": issue.get("priority", "Medium"),
            })
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, dict):
                # mcp-atlassian v0.21+ wraps result: {"message": "...", "issue": {"key": "KAN-32"}}
                issue_obj = parsed.get("issue", parsed)
                if issue_obj.get("key"):
                    return {"success": True, "issue_key": issue_obj["key"],
                            "message": f"Created {issue_obj['key']}"}
            return {"success": True, "message": f"Created: {str(raw)[:200]}"}
        except Exception as exc:
            return {"success": False, "message": f"Failed: {exc}"}

    @staticmethod
    def _parse_json_array(text: str) -> list[dict]:
        """Robustly extract a JSON array from LLM output."""
        text = text.strip()
        # Strip markdown fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0]
        text = text.strip()
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            # Try to find array within the text
            start = text.find("[")
            end = text.rfind("]")
            if start != -1 and end != -1:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    pass
            log.error("Failed to parse LLM JSON: %s", text[:500])
            return []


# ════════════════════════════════════════════════════════════════════════════
#  Entry points
# ════════════════════════════════════════════════════════════════════════════

async def _run_pipeline(
    spreadsheet_id: str = "",
    sheet_range: str = "",
    project_key: str = "",
) -> list[dict]:
    async with MCPClientManager().connect() as mcp:
        pipeline = SyncPipeline(mcp)
        return await pipeline.run(spreadsheet_id, sheet_range, project_key)


def run(
    spreadsheet_id: str = "",
    sheet_range: str = "",
    project_key: str = "",
) -> list[dict]:
    """Synchronous entry point for the backend pipeline."""
    return asyncio.run(_run_pipeline(spreadsheet_id, sheet_range, project_key))


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    pk = sys.argv[1] if len(sys.argv) > 1 else "KAN"
    results = run(project_key=pk)
    print(json.dumps(results, indent=2))
