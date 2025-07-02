import os, csv, json
from uuid import uuid4
from datetime import datetime as dt, timedelta, date
from flask import Flask, jsonify, request, render_template, send_file

app = Flask(__name__)

DATA_DIR    = os.path.dirname(os.path.abspath(__file__))
TIMERS_FILE = os.path.join(DATA_DIR, "timers.json")
LOG_FILE    = os.path.join(DATA_DIR, "timelog.csv")

# ───────────── helpers ──────────────────────────────────────────────────
def load_timers():
    if not os.path.exists(TIMERS_FILE):
        return []
    with open(TIMERS_FILE) as f:
        return json.load(f)

def save_timers(timers):
    with open(TIMERS_FILE, "w") as f:
        json.dump(timers, f, indent=2)

def ensure_log_header():
    if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0:
        with open(LOG_FILE, "w", newline="") as f:
            csv.writer(f).writerow(["timer_id", "timer_name", "action", "timestamp"])

def log_action(tid: str, name: str, act: str):
    ensure_log_header()
    with open(LOG_FILE, "a", newline="") as f:
        csv.writer(f).writerow([tid, name, act, dt.utcnow().isoformat()])

# ───────────── timers API ───────────────────────────────────────────────
@app.route("/api/timers", methods=["GET"])
def get_timers():
    timers = load_timers()
    now_ts = dt.utcnow().timestamp()
    for t in timers:
        if t.get("running"):
            start_ts = dt.fromisoformat(t["start_time"]).timestamp()
            t["elapsed"] = int(now_ts - start_ts)
        else:
            t["elapsed"] = 0
    return jsonify(timers)

@app.route("/api/timers", methods=["POST"])
def create_timer():
    name = request.json.get("name")
    if not name:
        return jsonify({"error": "Name required"}), 400
    timers = load_timers()
    timers.append({"id": str(uuid4()), "name": name,
                   "running": False, "start_time": None})
    save_timers(timers)
    return jsonify(timers[-1]), 201

@app.route("/api/timers/<tid>/toggle", methods=["POST"])
def toggle_timer(tid):
    action = request.json.get("action")
    if action not in ("start", "stop"):
        return jsonify({"error": "Invalid action"}), 400
    timers = load_timers()
    for t in timers:
        if t["id"] == tid:
            if action == "start":
                t.update(running=True, start_time=dt.utcnow().isoformat())
            else:
                t.update(running=False, start_time=None)
            save_timers(timers)
            log_action(tid, t["name"], action)
            return jsonify(t)
    return jsonify({"error": "Timer not found"}), 404

@app.route("/api/timers/<tid>", methods=["DELETE"])
def delete_timer(tid):
    save_timers([t for t in load_timers() if t["id"] != tid])
    return "", 204

# ───────────── CSV upload / download ────────────────────────────────────
@app.route("/upload", methods=["POST"])
def upload_csv():
    f = request.files.get("file")
    if f and f.filename.endswith(".csv"):
        f.save(LOG_FILE)
    ensure_log_header()
    return "", 204

@app.route("/download")
def download_csv():
    ensure_log_header()
    return send_file(LOG_FILE, mimetype="text/csv",
                     as_attachment=True, download_name="timelog.csv")

# ───────────── /api/stats ───────────────────────────────────────────────
@app.route("/api/stats", methods=["GET"])
def api_stats():
    """
    period  = day | week | month | all         (default: day)
    granular= hourbits   -> include 168-bool list per tracker
    week, year           -> pick specific ISO week for period=week
    """
    ensure_log_header()
    period   = request.args.get("period", "day")
    granular = request.args.get("granular")        # “hourbits” expected by UI
    today    = date.today()

    # ---------- figure out week span ----------
    if period == "week":
        w = int(request.args.get("week", 0) or 0)
        y = int(request.args.get("year", 0) or 0)
        monday = date.fromisocalendar(y, w, 1) if w and y else today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        week_start = dt.combine(monday, dt.min.time())   # Mon 00:00
        week_end   = week_start + timedelta(days=7)      # next Mon 00:00
    else:
        monday = sunday = week_start = week_end = None

    # ---------- accumulators ----------
    secs_per_tracker_day: dict[str, dict[str, float]] = {}
    bits_per_tracker:    dict[str, list] = {}
    open_start:          dict[str, dt]   = {}

    # ---------- scan CSV ----------
    with open(LOG_FILE) as f:
        for row in csv.DictReader(f):
            try:
                ts = dt.fromisoformat(row["timestamp"])
            except ValueError:
                continue
            tid, name, act = row["timer_id"], row["timer_name"], row["action"]

            if act == "start":
                open_start[tid] = ts
                continue
            if act != "stop":
                continue

            start = open_start.pop(tid, None)
            if not start:
                continue
            end = ts

            # ---- clip to week (if requested) ----
            if period == "week":
                # any overlap with [week_start, week_end)?
                if end <= week_start or start >= week_end:
                    continue
                start = max(start, week_start)
                end   = min(end,   week_end)

            # ---- seconds per day (table) ----
            cur = start
            while cur < end:
                next_midnight = dt.combine(cur.date() + timedelta(days=1), dt.min.time())
                slice_end = min(next_midnight, end)
                secs = (slice_end - cur).total_seconds()
                secs_per_tracker_day.setdefault(name, {}).setdefault(cur.date().isoformat(), 0)
                secs_per_tracker_day[name][cur.date().isoformat()] += secs
                cur = slice_end

            # ---- precise hour bits (168) ----
            if period == "week":
                bits = bits_per_tracker.setdefault(name, [False] * 168)
                first_idx = int((start - week_start).total_seconds() // 3600)
                last_idx  = int((end   - week_start).total_seconds() // 3600)
                for idx in range(first_idx, min(last_idx + 1, 168)):
                    bits[idx] = True

    # ---------- build weekly response ----------
    if period == "week":
        labels   = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
        trackers = []
        for name, daymap in secs_per_tracker_day.items():
            hours = [round(daymap.get(d, 0) / 3600, 2) for d in labels]
            t_obj = {"name": name, "hours": hours}
            if granular == "hourbits":
                t_obj["hourBits"] = bits_per_tracker.get(name, [False] * 168)
            trackers.append(t_obj)
        return jsonify({"labels": labels, "trackers": trackers})

    # ---------- other periods ----------
    aggregate = [{"name": n,
                  "hours": round(sum(daymap.values()) / 3600, 2)}
                 for n, daymap in secs_per_tracker_day.items()]
    return jsonify(aggregate)

# ───────────── pages ─────────────────────────────────────────────────────
@app.route("/stats")
def stats_page():
    return render_template("stats.html")

@app.route("/")
def index_page():
    return render_template("index.html")

# ───────────── run ───────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run("0.0.0.0", 8000, debug=True)
