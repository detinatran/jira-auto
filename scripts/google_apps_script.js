/**
 * Google Apps Script - Auto-sync trigger
 * 
 * Deploy this script in your Google Sheet to automatically trigger
 * sync whenever the sheet is edited.
 * 
 * Setup:
 * 1. Open your Google Sheet
 * 2. Extensions → Apps Script
 * 3. Paste this code
 * 4. Set up trigger: Run → onEdit (when sheet is edited)
 * 5. Deploy as Web App (optional, for manual trigger)
 */

// Configuration
const WEBHOOK_URL = 'https://your-server.com/webhook/sheet';
const WEBHOOK_SECRET = 'your-secret-token'; // Same as .env WEBHOOK_SECRET

/**
 * Triggered automatically when sheet is edited
 */
function onEdit(e) {
  // Only trigger for Tasks sheet
  const sheet = e.source.getActiveSheet();
  const sheetName = sheet.getName();
  
  if (sheetName !== 'Tasks') {
    Logger.log(`Ignoring edit in sheet: ${sheetName}`);
    return;
  }
  
  // Get edited range
  const range = e.range;
  const row = range.getRow();
  const col = range.getColumn();
  
  Logger.log(`Edit detected: Row ${row}, Col ${col}`);
  
  // Only trigger if editing task data (not headers)
  if (row > 1) {
    triggerWebhook('EDIT', {
      sheet: sheetName,
      row: row,
      column: col,
      value: e.value
    });
  }
}

/**
 * Triggered when rows are inserted/deleted
 */
function onChange(e) {
  const changeType = e.changeType;
  Logger.log(`Change detected: ${changeType}`);
  
  if (changeType === 'INSERT_ROW' || changeType === 'REMOVE_ROW') {
    triggerWebhook(changeType, {
      sheet: e.source.getActiveSheet().getName()
    });
  }
}

/**
 * Send webhook to sync server
 */
function triggerWebhook(changeType, metadata) {
  const payload = {
    secret: WEBHOOK_SECRET,
    changeType: changeType,
    timestamp: new Date().toISOString(),
    metadata: metadata
  };
  
  const options = {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };
  
  try {
    const response = UrlFetchApp.fetch(WEBHOOK_URL, options);
    const code = response.getResponseCode();
    
    if (code === 200) {
      Logger.log('Webhook triggered successfully');
    } else {
      Logger.log(`Webhook failed: ${code} - ${response.getContentText()}`);
    }
  } catch (error) {
    Logger.log(`Webhook error: ${error.message}`);
  }
}

/**
 * Manual trigger for testing
 */
function testWebhook() {
  triggerWebhook('MANUAL_TEST', { test: true });
}

/**
 * Setup instructions (run once)
 */
function setupTriggers() {
  // Delete existing triggers
  const triggers = ScriptApp.getProjectTriggers();
  triggers.forEach(trigger => ScriptApp.deleteTrigger(trigger));
  
  // Create onEdit trigger
  ScriptApp.newTrigger('onEdit')
    .forSpreadsheet(SpreadsheetApp.getActive())
    .onEdit()
    .create();
  
  // Create onChange trigger
  ScriptApp.newTrigger('onChange')
    .forSpreadsheet(SpreadsheetApp.getActive())
    .onChange()
    .create();
  
  Logger.log('Triggers set up successfully!');
}
