#!/usr/bin/env python3
"""
main.py — CLI entry point for the Jira ↔ Sheet integration system.

Usage:
  ── Legacy (direct API) ──────────────────────────────────────────
    python main.py sync          Sync pending tasks to Jira
    python main.py chat          Start the LLM agent chat (direct API)
    python main.py report        Print full analytics report
    python main.py dashboard     Print dashboard summary only
    python main.py export        Export report as JSON
    python main.py tasks         Show all tasks table
    python main.py overdue       Show overdue tasks
    python main.py team          Show team workload
    python main.py logs          Show sync log

  ── MCP-based (new architecture) ────────────────────────────────
    python main.py agent         Step 1 — Agent-Driven  (LLM → MCP → Service)
    python main.py pipeline      Step 2 — Backend Pipeline (Sheet → LLM → Jira)
    python main.py react         Step 3 — ReAct Agent Loop
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from rich.console import Console

from src.utils import config

console = Console()


def cmd_sync(args):
    """Run Sheet → Jira sync for all pending tasks."""
    from src.core import sync_service
    console.print("[bold cyan]Syncing pending tasks to Jira...[/]\n")

    if not config.validate_jira_config():
        console.print("[yellow][WARN] Jira credentials not configured → running dry-run mode[/]\n")

    results = sync_service.run_sync()

    if not results:
        console.print("[green]No pending tasks to sync.[/]")
        return

    for r in results:
        icon = "[OK]" if r["success"] else "[FAIL]"
        console.print(f"  {icon} [{r['action'].upper():6s}] {r['task_id']} → {r.get('issue_key', 'N/A')}: {r['message']}")

    console.print(f"\n[bold]Synced {sum(1 for r in results if r['success'])}/{len(results)} tasks.[/]")


def cmd_report(args):
    """Print the full analytics report."""
    from src.core import reporting
    reporting.full_report()


def cmd_dashboard(args):
    """Print dashboard summary."""
    from src.core import reporting
    reporting.print_dashboard()


def cmd_chat(args):
    """Start the interactive LLM agent chat."""
    if not config.validate_ai_config():
        console.print(
            "[bold red]Error:[/] No AI API key configured.\n"
            "Please set OPENAI_API_KEY or GOOGLE_API_KEY in your .env file.\n"
        )
        sys.exit(1)

    from src.agents import llm_agent
    llm_agent.interactive_chat()


def cmd_export(args):
    """Export the analytics report as JSON."""
    from src.core import reporting
    output = args.output or "report.json"
    report_json = reporting.export_report_json()

    if output == "-":
        print(report_json)
    else:
        with open(output, "w") as f:
            f.write(report_json)
        console.print(f"[green]Report exported to {output}[/]")


def cmd_tasks(args):
    """Show all tasks table."""
    from src.core import reporting
    reporting.print_task_table()


def cmd_overdue(args):
    """Show overdue tasks."""
    from src.core import reporting
    reporting.print_overdue_tasks()


def cmd_team(args):
    """Show team workload."""
    from src.core import reporting
    reporting.print_team_workload()


def cmd_logs(args):
    """Show sync log."""
    from src.core import reporting
    reporting.print_sync_log()


# ════════════════════════════════════════════════════════════════════════════
#  MCP-based commands (new architecture)
# ════════════════════════════════════════════════════════════════════════════

def cmd_agent(args):
    """Step 1 — Agent-Driven: LLM → MCP → Service."""
    if not config.validate_ai_config():
        console.print(
            "[bold red]Error:[/] No AI API key configured.\n"
            "Set OPENAI_API_KEY or GOOGLE_API_KEY in .env\n"
        )
        sys.exit(1)

    from src.agents.mcp_agent import interactive_chat
    interactive_chat()


def cmd_pipeline(args):
    """Step 2 — Backend Pipeline: Sheet → LLM → Jira."""
    if not config.validate_ai_config():
        console.print("[bold red]Error:[/] No AI API key configured.\n")
        sys.exit(1)

    from src.core.pipeline import run

    project_key = args.project_key
    sheet_range = args.range
    spreadsheet_id = args.sheet_id or config.GOOGLE_SHEET_ID

    console.print("[bold cyan]Running backend pipeline (Sheet → LLM → Jira)…[/]\n")
    console.print(f"  Sheet ID   : {spreadsheet_id}")
    console.print(f"  Range      : {sheet_range}")
    console.print(f"  Project Key: {project_key}\n")

    results = run(
        spreadsheet_id=spreadsheet_id,
        sheet_range=sheet_range,
        project_key=project_key,
    )

    for r in results:
        icon = "[OK]" if r.get("success") else "[FAIL]"
        console.print(f"  {icon} Row {r.get('row', '?')}: {r.get('summary', '')} → {r.get('message', '')}")

    ok = sum(1 for r in results if r.get("success"))
    console.print(f"\n[bold]Pipeline complete: {ok}/{len(results)} issues created.[/]")


def cmd_react(args):
    """Step 3 — ReAct Agent: Reasoning + Acting loop."""
    if not config.validate_ai_config():
        console.print("[bold red]Error:[/] No AI API key configured.\n")
        sys.exit(1)

    if args.goal:
        # One-shot mode
        from src.agents.react_agent import run_goal
        console.print("[bold cyan]Running ReAct agent…[/]\n")
        result = run_goal(args.goal, verbose=True)
        from rich.markdown import Markdown
        console.print(Markdown(result))
    else:
        # Interactive mode
        from src.agents.react_agent import interactive_react
        interactive_react()


def main():
    parser = argparse.ArgumentParser(
        description="Jira ↔ Sheet Integration with LLM Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # sync
    sub.add_parser("sync", help="Sync pending tasks from Sheet to Jira")

    # report
    sub.add_parser("report", help="Print full analytics report")

    # dashboard
    sub.add_parser("dashboard", help="Print dashboard summary")

    # chat
    sub.add_parser("chat", help="Start LLM agent interactive chat")

    # export
    p_export = sub.add_parser("export", help="Export report as JSON")
    p_export.add_argument("-o", "--output", default="report.json",
                          help="Output file (use '-' for stdout)")

    # tasks
    sub.add_parser("tasks", help="Show all tasks")

    # overdue
    sub.add_parser("overdue", help="Show overdue tasks")

    # team
    sub.add_parser("team", help="Show team workload")

    # logs
    sub.add_parser("logs", help="Show sync log")

    # ── MCP-based commands ──────────────────────────────────────────────
    sub.add_parser("agent", help="Step 1: Agent-Driven (LLM → MCP → Service)")

    p_pipeline = sub.add_parser("pipeline", help="Step 2: Backend Pipeline (Sheet → LLM → Jira)")
    p_pipeline.add_argument("-p", "--project-key", default="KAN",
                            help="Jira project key (default: KAN)")
    p_pipeline.add_argument("-r", "--range", default="Tasks",
                            help="Sheet tab / range (default: Tasks)")
    p_pipeline.add_argument("-s", "--sheet-id", default="",
                            help="Google Sheet ID (defaults to .env value)")

    p_react = sub.add_parser("react", help="Step 3: ReAct Agent Loop")
    p_react.add_argument("goal", nargs="?", default="",
                         help="Goal for one-shot mode (omit for interactive)")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    commands = {
        # Legacy (direct API)
        "sync": cmd_sync,
        "report": cmd_report,
        "dashboard": cmd_dashboard,
        "chat": cmd_chat,
        "export": cmd_export,
        "tasks": cmd_tasks,
        "overdue": cmd_overdue,
        "team": cmd_team,
        "logs": cmd_logs,
        # MCP-based (new architecture)
        "agent": cmd_agent,
        "pipeline": cmd_pipeline,
        "react": cmd_react,
    }

    if not args.command:
        # Default: show dashboard + quick help
        console.print("[bold cyan]Jira ↔ Sheet Integration System[/]\n")
        cmd_dashboard(args)
        console.print("Run [bold]python main.py --help[/] to see all commands.\n")
        return

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
