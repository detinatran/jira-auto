#!/usr/bin/env python3
"""
test_llm_flow.py — End-to-end LLM flow test for all 3 architectures.

Tests each step with REAL LLM calls but READ-ONLY operations
(no Jira issues are created, no sheet data is modified).

Run:
    .venv/bin/python3 test_llm_flow.py
    .venv/bin/python3 test_llm_flow.py --step 1   # only Step 1
    .venv/bin/python3 test_llm_flow.py --step 2   # only Step 2
    .venv/bin/python3 test_llm_flow.py --step 3   # only Step 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from rich.console import Console
from rich.rule import Rule
from rich.panel import Panel
from rich.markdown import Markdown

logging.basicConfig(level=logging.WARNING)   # quiet – we show our own output

# Suppress FastMCP startup banners (they go to stderr which we redirect)
import os
os.environ.setdefault("FASTMCP_LOG_LEVEL", "ERROR")
os.environ.setdefault("MCP_LOG_LEVEL", "ERROR")
console = Console()


# ════════════════════════════════════════════════════════════════════════════
#  Step 2 — LLM transform test (NO MCP, NO Jira write)
#  Sheet data is read directly via gspread, then LLM maps it.
# ════════════════════════════════════════════════════════════════════════════

def test_step2_transform():
    """
    Read real Tasks tab from Google Sheet, send to LLM for mapping,
    print the JSON payloads it produces.  Does NOT call Jira.
    """
    console.print(Rule("[bold cyan]Step 2 — Backend Pipeline  (Sheet → LLM transform)[/]"))

    from src.utils import config
    from src.core.sheet_reader import load_sheet, tasks_to_dicts
    from src.core.pipeline import SyncPipeline, TRANSFORM_PROMPT

    # ── 1. Read sheet ────────────────────────────────────────────────────
    console.print("[dim]Loading sheet data…[/]")
    t0 = time.perf_counter()
    data = load_sheet()
    tasks_raw = tasks_to_dicts(data.tasks[:5])   # first 5 rows for speed
    elapsed = time.perf_counter() - t0
    console.print(f"  {len(tasks_raw)} tasks loaded in {elapsed:.2f}s")

    project_key = (data.projects[0].jira_project_key
                   if data.projects else "KAN")
    console.print(f"  Project key: [cyan]{project_key}[/]")

    # ── 2. Show raw sheet rows ───────────────────────────────────────────
    console.print("\n[bold]Raw sheet rows (input):[/]")
    console.print(json.dumps(tasks_raw, indent=2, ensure_ascii=False))

    # ── 3. LLM transform ─────────────────────────────────────────────────
    console.print("\n[dim]Calling LLM to transform rows into Jira payloads…[/]")

    # Reuse the pipeline's _transform logic directly
    class _FakeMCP:
        pass

    pipeline = object.__new__(SyncPipeline)  # bypass __init__
    pipeline._mcp = _FakeMCP()

    if config.OPENAI_API_KEY:
        from openai import OpenAI
        pipeline._provider = "openai"
        pipeline._client = OpenAI(api_key=config.OPENAI_API_KEY)
        pipeline._model = "gpt-4o-mini"
        provider_label = f"OpenAI / {pipeline._model}"
    else:
        from google import genai
        pipeline._provider = "gemini"
        pipeline._client = genai.Client(api_key=config.GOOGLE_API_KEY)
        pipeline._model = "gemini-2.5-flash"
        provider_label = f"Gemini / {pipeline._model}"

    console.print(f"  Provider: [cyan]{provider_label}[/]")
    t0 = time.perf_counter()
    payloads = pipeline._transform(tasks_raw, project_key)
    elapsed = time.perf_counter() - t0

    # ── 4. Show result ────────────────────────────────────────────────────
    console.print(f"\n[bold]LLM output — Jira payloads ({elapsed:.2f}s):[/]")
    console.print(json.dumps(payloads, indent=2, ensure_ascii=False))

    ok = len(payloads) > 0 and all("summary" in p for p in payloads)
    status = "PASS" if ok else "FAIL (no payloads or missing 'summary')"
    console.print(f"\n  {status}  —  {len(payloads)} payloads produced")
    return ok


# ════════════════════════════════════════════════════════════════════════════
#  Step 1 — MCP Agent read-only query
# ════════════════════════════════════════════════════════════════════════════

async def _test_step1_async():
    from src.utils import config
    from src.mcp.client import MCPClientManager
    from src.agents.mcp_agent import MCPAgent

    console.print(Rule("[bold cyan]Step 1 — Agent-Driven  (LLM → MCP → Service)[/]"))

    question = (
        "List the sheet tabs available and show me the first 3 rows "
        f"from the Tasks tab of spreadsheet ID '{config.GOOGLE_SHEET_ID}'."
    )
    console.print(f"[bold]Question:[/] {question}\n")

    async with MCPClientManager().connect() as mcp:
        tool_count = len(mcp.list_tool_names())
        console.print(f"  MCP tools available: [cyan]{tool_count}[/]\n")

        agent = MCPAgent(mcp)
        prov = "OpenAI" if agent._provider == "openai" else "Gemini"
        console.print(f"  Provider: [cyan]{prov} / {agent._model}[/]\n")
        console.print("[dim]Running agent…[/]\n")

        t0 = time.perf_counter()
        reply = await agent.send(question)
        elapsed = time.perf_counter() - t0

    console.print(Panel(Markdown(reply), title="Agent reply", border_style="green"))
    console.print(f"\n  Time: {elapsed:.2f}s")
    ok = bool(reply) and "error" not in reply.lower()[:50]
    console.print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_step1_agent():
    return asyncio.run(_test_step1_async())


# ════════════════════════════════════════════════════════════════════════════
#  Step 3 — ReAct agent read-only goal
# ════════════════════════════════════════════════════════════════════════════

async def _test_step3_async():
    from src.utils import config
    from src.mcp.client import MCPClientManager
    from src.agents.react_agent import ReactAgent

    console.print(Rule("[bold cyan]Step 3 — ReAct Agent Loop  (Reasoning + Acting)[/]"))

    goal = (
        f"Read the Tasks tab from Google Sheet '{config.GOOGLE_SHEET_ID}' "
        "and tell me: how many tasks are there and what are their statuses? "
        "IMPORTANT: only use READ tools (get_sheet_data, list_sheets). "
        "Do NOT write, update, or modify the spreadsheet in any way."
    )
    console.print(f"[bold]Goal:[/] {goal}\n")

    async with MCPClientManager().connect() as mcp:
        agent = ReactAgent(mcp)
        prov = "OpenAI" if agent._provider == "openai" else "Gemini"
        console.print(f"  Provider: [cyan]{prov} / {agent._model}[/]\n")
        console.print("[dim]Running ReAct loop…[/]\n")

        t0 = time.perf_counter()
        result = await agent.run(goal, verbose=True)
        elapsed = time.perf_counter() - t0

    console.print()
    console.print(Panel(Markdown(result), title="Final Answer", border_style="green"))
    console.print(f"\n  Time: {elapsed:.2f}s")
    ok = bool(result) and len(result) > 20
    console.print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


def test_step3_react():
    return asyncio.run(_test_step3_async())


# ════════════════════════════════════════════════════════════════════════════
#  Main
# ════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Test LLM flows end-to-end")
    parser.add_argument("--step", type=int, choices=[1, 2, 3],
                        help="Run only this step (default: all)")
    args = parser.parse_args()

    results: dict[int, bool] = {}

    steps = [args.step] if args.step else [2, 1, 3]

    for step in steps:
        console.print()
        try:
            if step == 2:
                results[2] = test_step2_transform()
            elif step == 1:
                results[1] = test_step1_agent()
            elif step == 3:
                results[3] = test_step3_react()
        except Exception as exc:
            console.print(f"[bold red]  EXCEPTION:[/] {exc}")
            import traceback
            traceback.print_exc()
            results[step] = False

    # ── Summary ──────────────────────────────────────────────────────────
    console.print()
    console.print(Rule("[bold]Test Summary[/]"))
    label = {2: "Step 2 Backend Pipeline (LLM transform)",
             1: "Step 1 Agent-Driven     (MCP agent)",
             3: "Step 3 ReAct Loop       (Reasoning+Acting)"}
    all_ok = True
    for s in steps:
        ok = results.get(s, False)
        all_ok = all_ok and ok
        icon = "[OK]" if ok else "[FAIL]"
        console.print(f"  {icon}  {label[s]}")

    console.print()
    if all_ok:
        console.print("[bold green]  All steps passed.[/]")
    else:
        console.print("[bold red]  Some steps failed.[/]")
        sys.exit(1)


if __name__ == "__main__":
    main()
