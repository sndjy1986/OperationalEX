
from flask import Flask, render_template, request, redirect, url_for, session
import json, os
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)
app.secret_key = "very_secret_key"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "ADMIN123")

CONFIG_PATH = "truck_config.json"
LOG_PATH = "logs/activity.log"
LOG_RETENTION_HOURS = 72

truck_data = {}
truck_status = {}
logistics_timer = {}
activity_log = []

def load_config():
    global truck_data, truck_status
    with open(CONFIG_PATH) as f:
        truck_data = json.load(f)
    if "trucks" not in truck_data:
        truck_data["trucks"] = []
    truck_status.clear()
    for truck in truck_data["trucks"]:
        truck_status[truck["id"]] = "available"

def update_status(truck_id, new_status):
    truck_status[truck_id] = new_status
    if new_status == "available":
        logistics_timer.pop(truck_id, None)
    log_action(truck_id, new_status)

def log_action(truck_id, new_status):
    os.makedirs("logs", exist_ok=True)
    now = datetime.now(pytz.timezone("US/Eastern"))
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {truck_id} → {new_status}"
    activity_log.insert(0, entry)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/status")
def system_status():
    now = datetime.utcnow()
    flash_trucks = {}
    logistics_times = {}
    for truck_id, status in truck_status.items():
        if status in ["logistics", "destination"]:
            start_time = logistics_timer.get(truck_id)
            if start_time:
                logistics_times[truck_id] = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                if (status == "logistics" and now - start_time >= timedelta(minutes=10)) or                    (status == "destination" and now - start_time >= timedelta(minutes=20)):
                    flash_trucks[truck_id] = True
    available_trucks = sum(1 for tid, s in truck_status.items() if s == "available")
    return render_template("status.html", trucks=truck_data["trucks"], status=truck_status,
                           available_trucks=available_trucks,
                           flash_trucks=flash_trucks,
                           logistics_times=logistics_times)

@app.route("/availability", methods=["GET", "POST"])
def availability():
    if request.method == "POST":
        selected = request.form.getlist("available")
        for truck in truck_status:
            if truck_status[truck] not in ["out", "logistics", "destination"]:
                update_status(truck, "available" if truck in selected else "unavailable")
        return redirect(url_for("index"))
    return render_template("availability.html", trucks=truck_data["trucks"], status=truck_status)

@app.route("/dispatch", methods=["POST"])
def dispatch():
    truck_id = request.form["truck_id"]
    update_status(truck_id, "out")
    return render_template("result.html", dispatched=truck_id, fallback=None)

@app.route("/reset/<truck_id>")
def reset_truck(truck_id):
    update_status(truck_id, "available")
    logistics_timer.pop(truck_id, None)
    return redirect(url_for("index"))

@app.route("/logistics/<truck_id>")
def make_logistics(truck_id):
    update_status(truck_id, "logistics")
    logistics_timer[truck_id] = datetime.utcnow()
    return redirect(url_for("index"))

@app.route("/destination/<truck_id>")
def make_destination(truck_id):
    update_status(truck_id, "destination")
    logistics_timer[truck_id] = datetime.utcnow()
    return redirect(url_for("index"))

if __name__ == "__main__":
    load_config()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
