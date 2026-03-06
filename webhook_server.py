#!/usr/bin/env python3
"""
webhook_server.py - Flask server to receive webhooks from Jira and Google Sheets.

Auto-triggers sync when:
- Jira issue is created/updated
- Google Sheet is modified (via Apps Script trigger)
"""

import os
import logging
from flask import Flask, request, jsonify
from src.core import sync_service
from src.utils import config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)

app = Flask(__name__)

# Secret token for authentication (set in .env)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "your-secret-token")


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "service": "jira-sync-webhook"}), 200


@app.route("/webhook/jira", methods=["POST"])
def jira_webhook():
    """Handle Jira webhooks (issue created, updated, deleted)."""
    # Verify secret token
    auth_header = request.headers.get("Authorization")
    if auth_header != f"Bearer {WEBHOOK_SECRET}":
        log.warning("Unauthorized Jira webhook attempt")
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    event_type = data.get("webhookEvent")
    issue = data.get("issue", {})
    issue_key = issue.get("key")
    
    log.info(f"Jira webhook: {event_type} for {issue_key}")
    
    # Trigger sync for relevant events
    if event_type in ["jira:issue_created", "jira:issue_updated"]:
        try:
            # Run sync in background (in production, use Celery/RQ)
            results = sync_service.run_sync()
            log.info(f"Auto-sync completed: {len(results)} tasks processed")
            return jsonify({
                "status": "success",
                "message": f"Synced {len(results)} tasks",
                "issue_key": issue_key
            }), 200
        except Exception as e:
            log.exception("Sync failed")
            return jsonify({"error": str(e)}), 500
    
    return jsonify({"status": "ignored", "event": event_type}), 200


@app.route("/webhook/sheet", methods=["POST"])
def sheet_webhook():
    """Handle Google Sheets webhooks (triggered by Apps Script onChange)."""
    # Verify secret token
    secret = request.json.get("secret")
    if secret != WEBHOOK_SECRET:
        log.warning("Unauthorized Sheet webhook attempt")
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    change_type = data.get("changeType", "EDIT")
    
    log.info(f"Sheet webhook: {change_type}")
    
    try:
        # Trigger sync
        results = sync_service.run_sync()
        log.info(f"Auto-sync completed: {len(results)} tasks processed")
        return jsonify({
            "status": "success",
            "message": f"Synced {len(results)} tasks"
        }), 200
    except Exception as e:
        log.exception("Sync failed")
        return jsonify({"error": str(e)}), 500


@app.route("/trigger-sync", methods=["POST"])
def manual_trigger():
    """Manually trigger a sync (for testing or external automation)."""
    secret = request.json.get("secret") if request.json else None
    if secret != WEBHOOK_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        results = sync_service.run_sync()
        return jsonify({
            "status": "success",
            "synced": len(results),
            "results": results
        }), 200
    except Exception as e:
        log.exception("Sync failed")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    import os
    port = int(os.getenv("WEBHOOK_PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
