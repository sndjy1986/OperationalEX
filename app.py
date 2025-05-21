from flask import Flask, render_template, request, redirect, url_for, session
import json
import os
from datetime import datetime, timedelta
import pytz
from pathlib import Path

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "default_secret_key")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "ADMIN123")

CONFIG_PATH = os.getenv("CONFIG_PATH", "data/truck_config.json")
LOG_PATH = os.getenv("LOG_PATH", "logs/activity.log")
LOG_RETENTION_HOURS = 72

truck_data = {}
truck_status = {}
logistics_timer = {}
activity_log = []

def ensure_directories():
    Path(os.path.dirname(CONFIG_PATH)).mkdir(parents=True, exist_ok=True)
    Path(os.path.dirname(LOG_PATH)).mkdir(parents=True, exist_ok=True)

def load_config():
    global truck_data, truck_status
    ensure_directories()

    with open(CONFIG_PATH) as f:
        truck_data = json.load(f)

    if "trucks" not in truck_data:
        truck_data["trucks"] = []

    if "fallback_rules" not in truck_data:
        truck_data["fallback_rules"] = []

    truck_status.clear()
    for truck in truck_data["trucks"]:
        truck_status[truck["id"]] = "available"

def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=4)

def update_status(truck_id, new_status):
    truck_status[truck_id] = new_status
    if new_status == "available":
        logistics_timer.pop(truck_id, None)
    log_action(truck_id, new_status)

def log_action(truck_id, new_status):
    ensure_directories()
    eastern = pytz.timezone("US/Eastern")
    now = datetime.now(eastern)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {truck_id} → {new_status}"
    activity_log.insert(0, entry)

    entries = []
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, "r") as f:
            for line in f:
                try:
                    ts_str = line.split("]")[0][1:]
                    ts = eastern.localize(datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S"))
                    if (now - ts).total_seconds() <= LOG_RETENTION_HOURS * 3600:
                        entries.append(line.strip())
                except:
                    pass

    entries.append(entry)
    with open(LOG_PATH, "w") as f:
        for line in entries:
            f.write(line + "\n")

@app.route("/")
def index():
    if not truck_data.get("trucks"):
        return "🚨 ERROR: No truck data loaded! Check CONFIG_PATH and ensure truck_config.json is present.", 500

    now = datetime.utcnow()
    flash_trucks = {}
    logistics_times = {}

    for truck_id, status in truck_status.items():
        if status in ["logistics", "destination"]:
            start_time = logistics_timer.get(truck_id)
            if start_time:
                logistics_times[truck_id] = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                if (
                    status == "logistics" and now - start_time >= timedelta(minutes=10)
                ) or (
                    status == "destination" and now - start_time >= timedelta(minutes=20)
                ):
                    flash_trucks[truck_id] = True

    available_trucks = sum(
        1 for tid, status in truck_status.items()
        if status == "available" and tid.startswith("Medic ")
    )

    show_admin_alert = available_trucks <= 3

    return render_template("index.html",
        trucks=truck_data["trucks"],
        status=truck_status,
        flash_trucks=flash_trucks,
        logistics_times=logistics_timer,
        activity_log=activity_log,
        show_admin_alert=show_admin_alert,
        available_trucks=available_trucks
    )

@app.route("/status")
def system_status():
    available_trucks = sum(
        1 for tid, s in truck_status.items()
        if s == "available" and tid.startswith("Medic ")
    )
    return render_template("status.html",
        trucks=truck_data["trucks"],
        status=truck_status,
        available_trucks=available_trucks
    )

@app.route("/availability", methods=["GET", "POST"])
def availability():
    if request.method == "POST":
        selected = request.form.getlist("available")
        for truck in truck_status:
            if truck_status[truck] not in ["out", "logistics", "destination"]:
                new_status = "available" if truck in selected else "unavailable"
                update_status(truck, new_status)
        return redirect(url_for("index"))

    return render_template("availability.html", trucks=truck_data["trucks"], status=truck_status)

@app.route("/dispatch", methods=["POST"])
def dispatch():
    truck_id = request.form.get("truck_id")
    if not truck_id:
        return "No truck ID provided", 400

    update_status(truck_id, "out")
    available_trucks = sum(
        1 for tid, status in truck_status.items()
        if status == "available" and tid.startswith("Medic ")
    )
    system_status_level = min(available_trucks, 17)
    areas_to_cover = truck_data.get("Level_Movement", {}).get(str(system_status_level), [])

    return render_template("postings.html",
        system_status_level=system_status_level,
        trucks_to_post=areas_to_cover
    )

@app.route("/reset/<truck_id>")
def reset_truck(truck_id):
    if truck_id in truck_status:
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

@app.route("/admin", methods=["GET", "POST"])
def admin():
    if not session.get("logged_in"):
        if request.method == "POST" and request.form.get("password") == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect("/admin")
        return render_template("admin_login.html")

    if request.method == "POST":
        for truck in truck_data["trucks"]:
            new_id = request.form.get(f"id_{truck['id']}")
            if new_id and new_id != truck["id"]:
                old_id = truck["id"]
                truck["id"] = new_id
                truck_status[new_id] = truck_status.pop(old_id, "available")
                if old_id in logistics_timer:
                    logistics_timer[new_id] = logistics_timer.pop(old_id)

        for truck in truck_data["trucks"]:
            new_loc = request.form.get(f"location_{truck['id']}")
            if new_loc:
                truck["location"] = new_loc

        new_rules = []
        for truck in truck_data["trucks"]:
            fb_val = request.form.get(f"fallback_{truck['id']}", "")
            fb_list = [x.strip() for x in fb_val.split(",") if x.strip()]
            new_rules.append({"primary": truck["id"], "fallbacks": fb_list})
        truck_data["fallback_rules"] = new_rules
        save_config(truck_data)

    fallback_map = {rule["primary"]: ", ".join(rule["fallbacks"]) for rule in truck_data.get("fallback_rules", [])}
    return render_template("admin.html", trucks=truck_data["trucks"], fallback_map=fallback_map)

load_config()


if __name__ == "__main__":
    load_config()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
