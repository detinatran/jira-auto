#!/usr/bin/env python3
"""
main.py — CLI entry point for the Jira ↔ Sheet integration system.

Usage:
    python main.py sync          Sync pending tasks to Jira
    python main.py report        Print full analytics report
    python main.py dashboard     Print dashboard summary only
    python main.py chat          Start the LLM agent chat
    python main.py export        Export report as JSON
    python main.py tasks         Show all tasks table
    python main.py overdue       Show overdue tasks
    python main.py team          Show team workload
    python main.py logs          Show sync log
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from rich.console import Console

import config

console = Console()


def cmd_sync(args):
    """Run Sheet → Jira sync for all pending tasks."""
    import sync_service
    console.print("[bold cyan]🔄 Syncing pending tasks to Jira...[/]\n")

    if not config.validate_jira_config():
        console.print("[yellow]⚠️  Jira credentials not configured → running dry-run mode[/]\n")

    results = sync_service.run_sync()

    if not results:
        console.print("[green]✅ No pending tasks to sync.[/]")
        return

    for r in results:
        icon = "✅" if r["success"] else "❌"
        console.print(f"  {icon} [{r['action'].upper():6s}] {r['task_id']} → {r.get('issue_key', 'N/A')}: {r['message']}")

    console.print(f"\n[bold]Synced {sum(1 for r in results if r['success'])}/{len(results)} tasks.[/]")


def cmd_report(args):
    """Print the full analytics report."""
    import reporting
    reporting.full_report()


def cmd_dashboard(args):
    """Print dashboard summary."""
    import reporting
    reporting.print_dashboard()


def cmd_chat(args):
    """Start the interactive LLM agent chat."""
    if not config.validate_gemini_config():
        console.print(
            "[bold red]Error:[/] GOOGLE_API_KEY not set.\n"
            "Please add it to your .env file (see .env.example).\n"
        )
        sys.exit(1)

    import llm_agent
    llm_agent.interactive_chat()


def cmd_export(args):
    """Export the analytics report as JSON."""
    import reporting
    output = args.output or "report.json"
    report_json = reporting.export_report_json()

    if output == "-":
        print(report_json)
    else:
        with open(output, "w") as f:
            f.write(report_json)
        console.print(f"[green]✅ Report exported to {output}[/]")


def cmd_tasks(args):
    """Show all tasks table."""
    import reporting
    reporting.print_task_table()


def cmd_overdue(args):
    """Show overdue tasks."""
    import reporting
    reporting.print_overdue_tasks()


def cmd_team(args):
    """Show team workload."""
    import reporting
    reporting.print_team_workload()


def cmd_logs(args):
    """Show sync log."""
    import reporting
    reporting.print_sync_log()


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

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    commands = {
        "sync": cmd_sync,
        "report": cmd_report,
        "dashboard": cmd_dashboard,
        "chat": cmd_chat,
        "export": cmd_export,
        "tasks": cmd_tasks,
        "overdue": cmd_overdue,
        "team": cmd_team,
        "logs": cmd_logs,
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
