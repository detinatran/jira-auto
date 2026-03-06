# Auto-Sync Setup Guide

## Overview

This system supports automatic synchronization triggered by:
1. **Jira webhooks** - when issues are created/updated
2. **Google Sheets changes** - via Apps Script onChange trigger
3. **Manual triggers** - via API endpoint

## Architecture

```
Google Sheet Edit
    ↓
Apps Script onEdit trigger
    ↓
POST /webhook/sheet
    ↓
Flask Server (webhook_server.py)
    ↓
sync_service.run_sync()
    ↓
Update Jira/Sheet

Jira Issue Updated
    ↓
Jira Webhook
    ↓
POST /webhook/jira
    ↓
Flask Server
    ↓
sync_service.run_sync()
    ↓
Update Sheet
```

## Setup Instructions

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Add to `.env`:
```bash
WEBHOOK_SECRET=your-secret-token-change-this
WEBHOOK_PORT=5000
```

### 3. Start Webhook Server

```bash
python webhook_server.py
```

Server runs on `http://localhost:5000` by default.

### 4. Setup Google Sheets Trigger

1. Open your Google Sheet
2. Go to **Extensions → Apps Script**
3. Paste code from `scripts/google_apps_script.js`
4. Update these values:
   ```javascript
   const WEBHOOK_URL = 'https://your-domain.com/webhook/sheet';
   const WEBHOOK_SECRET = 'your-secret-token'; // Same as .env
   ```
5. Run `setupTriggers()` function once
6. Authorize the script

### 5. Setup Jira Webhook

1. Go to **Jira Settings → System → Webhooks**
2. Click **Create a webhook**
3. Configure:
   - **Name**: Auto Sync to Sheet
   - **Status**: Enabled
   - **URL**: `https://your-domain.com/webhook/jira`
   - **Events**: 
     - Issue → created
     - Issue → updated
   - **JQL**: (optional filter, e.g., `project = KAN`)
4. Add custom header:
   - **Authorization**: `Bearer your-secret-token`

### 6. Deploy to Production

#### Option A: Local with ngrok (Testing)

```bash
# Terminal 1: Start webhook server
python webhook_server.py

# Terminal 2: Expose via ngrok
ngrok http 5000
```

Use the ngrok URL in your webhook configs.

#### Option B: Deploy to Cloud

**Heroku:**
```bash
# Create Procfile
echo "web: python webhook_server.py" > Procfile

# Deploy
heroku create jira-sync-webhook
heroku config:set WEBHOOK_SECRET=your-secret-token
git push heroku main
```

**Railway/Render:**
- Connect GitHub repo
- Set environment variables
- Deploy automatically

**AWS Lambda + API Gateway:**
- Use Zappa or Serverless Framework
- Configure API Gateway endpoints

### 7. Test Webhooks

#### Test Sheet Webhook:
```bash
curl -X POST http://localhost:5000/webhook/sheet \
  -H "Content-Type: application/json" \
  -d '{
    "secret": "your-secret-token",
    "changeType": "EDIT",
    "timestamp": "2026-03-06T10:00:00Z"
  }'
```

#### Test Jira Webhook:
```bash
curl -X POST http://localhost:5000/webhook/jira \
  -H "Authorization: Bearer your-secret-token" \
  -H "Content-Type: application/json" \
  -d '{
    "webhookEvent": "jira:issue_updated",
    "issue": {"key": "KAN-1"}
  }'
```

#### Test Manual Trigger:
```bash
curl -X POST http://localhost:5000/trigger-sync \
  -H "Content-Type: application/json" \
  -d '{"secret": "your-secret-token"}'
```

## Monitoring

### Health Check
```bash
curl http://localhost:5000/health
```

### View Logs
```bash
# If using systemd
journalctl -u jira-sync-webhook -f

# If running directly
# Logs to stdout/stderr
```

## Security Best Practices

1. **Use HTTPS in production** - Never expose HTTP endpoints publicly
2. **Strong webhook secret** - Use a long random string
3. **IP Whitelisting** - Restrict webhook endpoints to Jira IPs
4. **Rate limiting** - Prevent webhook flooding
5. **Environment variables** - Never commit secrets to git

## Troubleshooting

### Webhook Not Triggering

1. Check server is running: `curl http://localhost:5000/health`
2. Verify secret token matches in all configs
3. Check firewall rules allow incoming connections
4. Test manually with curl commands above

### Sync Errors

1. Check logs for error messages
2. Verify Jira/Sheet credentials are valid
3. Test sync manually: `python main.py sync`
4. Check network connectivity to APIs

### Google Apps Script Timeout

- Apps Script has 6-minute execution limit
- If sync takes too long, consider:
  - Queuing sync jobs (use Celery/RQ)
  - Batch processing smaller chunks
  - Async webhook responses

## Alternative: Cron-based Sync

If webhooks are not feasible, use periodic sync:

```bash
# Add to crontab
# Sync every 5 minutes
*/5 * * * * cd /path/to/jira && /path/to/.venv/bin/python main.py sync

# Sync every hour
0 * * * * cd /path/to/jira && /path/to/.venv/bin/python main.py sync
```

## Advanced: Background Job Queue

For production systems with high load:

```python
# Install Celery
pip install celery redis

# celery_tasks.py
from celery import Celery
from src.core import sync_service

app = Celery('jira_sync', broker='redis://localhost:6379/0')

@app.task
def async_sync():
    return sync_service.run_sync()

# webhook_server.py
from celery_tasks import async_sync

@app.route("/webhook/sheet", methods=["POST"])
def sheet_webhook():
    # Queue job instead of running inline
    async_sync.delay()
    return jsonify({"status": "queued"}), 202
```

## Endpoints Summary

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check |
| `/webhook/jira` | POST | Jira webhook receiver |
| `/webhook/sheet` | POST | Google Sheets webhook receiver |
| `/trigger-sync` | POST | Manual sync trigger |

## Next Steps

1. Set up monitoring/alerting (Sentry, Datadog, etc.)
2. Add retry logic for failed syncs
3. Implement webhook signature verification
4. Add metrics/analytics dashboard
5. Support bidirectional sync (Jira → Sheet)
