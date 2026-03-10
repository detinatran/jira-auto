"""
Configuration module — loads settings from .env file.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root (go up 2 levels from src/utils/)
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path)

# ── Jira ────────────────────────────────────────────────────────────────────
JIRA_URL: str = os.getenv("JIRA_URL", "https://your-domain.atlassian.net")
JIRA_EMAIL: str = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN: str = os.getenv("JIRA_API_TOKEN", "")
JIRA_DEFAULT_PROJECT: str = os.getenv("JIRA_DEFAULT_PROJECT", "")

# ── Google AI Studio (Gemini) ──────────────────────────────────────────────
GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")

# ── OpenAI API ──────────────────────────────────────────────────────────────
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

# ── Google Sheet ────────────────────────────────────────────────────────────
GOOGLE_SHEET_ID: str = os.getenv(
    "GOOGLE_SHEET_ID", "18parvs_us8AR9GS_ORZUtvGtkFND9qaF"
)
GOOGLE_SERVICE_ACCOUNT_FILE: str = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    str(Path(__file__).resolve().parent.parent.parent / "service_account.json"),
)

# ── MCP Server commands ────────────────────────────────────────────────────
MCP_SHEET_COMMAND: str = os.getenv("MCP_SHEET_COMMAND", "mcp-google-sheets")
MCP_JIRA_COMMAND: str = os.getenv("MCP_JIRA_COMMAND", "mcp-atlassian")

# ── Validation helpers ─────────────────────────────────────────────────────
def validate_jira_config() -> bool:
    """Return True when all Jira creds are present."""
    return bool(JIRA_URL and JIRA_EMAIL and JIRA_API_TOKEN)


def validate_gemini_config() -> bool:
    """Return True when the Google AI Studio key is present."""
    return bool(GOOGLE_API_KEY)


def validate_openai_config() -> bool:
    """Return True when the OpenAI API key is present."""
    return bool(OPENAI_API_KEY)


def validate_ai_config() -> bool:
    """Return True when either Gemini or OpenAI key is present."""
    return bool(GOOGLE_API_KEY or OPENAI_API_KEY)


def validate_google_sheet_config() -> bool:
    """Return True when Google Sheet ID and service-account file exist."""
    return bool(
        GOOGLE_SHEET_ID
        and GOOGLE_SERVICE_ACCOUNT_FILE
        and Path(GOOGLE_SERVICE_ACCOUNT_FILE).exists()
    )
