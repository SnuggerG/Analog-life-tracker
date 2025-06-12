import os
import csv
import json
import datetime
from datetime import datetime as dt
from uuid import uuid4
from flask import Flask, render_template, jsonify, request, send_file

app = Flask(__name__)

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
TIMERS_FILE = os.path.join(DATA_DIR, 'timers.json')
LOG_FILE = os.path.join(DATA_DIR, 'timelog.csv')

# --- Helpers ---
def load_timers():
    if not os.path.exists(TIMERS_FILE):
        return []
    with open(TIMERS_FILE, 'r') as f:
        return json.load(f)

def save_timers(timers):
    with open(TIMERS_FILE, 'w') as f:
        json.dump(timers, f, indent=2)

def ensure_log_header():
    # Create CSV with header if missing or empty
    if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0:
        with open(LOG_FILE, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['timer_id', 'timer_name', 'action', 'timestamp'])

def log_action(timer_id, timer_name, action):
    ensure_log_header()
    ts = dt.utcnow().isoformat()
    with open(LOG_FILE, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([timer_id, timer_name, action, ts])

# --- Timer Endpoints ---
@app.route('/api/timers', methods=['GET'])
def get_timers():
    timers = load_timers()
    now = dt.utcnow().timestamp()
    for t in timers:
        if t.get('running'):
            start_ts = dt.fromisoformat(t['start_time']).timestamp()
            t['elapsed'] = int(now - start_ts)
        else:
            t['elapsed'] = 0
    return jsonify(timers)

@app.route('/api/timers', methods=['POST'])
def create_timer():
    data = request.get_json()
    name = data.get('name')
    if not name:
        return jsonify({'error': 'Name required'}), 400
    timers = load_timers()
    new_timer = {
        'id': str(uuid4()),
        'name': name,
        'running': False,
        'start_time': None
    }
    timers.append(new_timer)
    save_timers(timers)
    return jsonify(new_timer), 201

@app.route('/api/timers/<timer_id>/toggle', methods=['POST'])
def toggle_timer(timer_id):
    data = request.get_json()
    action = data.get('action')
    if action not in ('start', 'stop'):
        return jsonify({'error': 'Invalid action'}), 400
    timers = load_timers()
    for t in timers:
        if t['id'] == timer_id:
            if action == 'start':
                t['running'] = True
                t['start_time'] = dt.utcnow().isoformat()
            else:
                t['running'] = False
                t['start_time'] = None
            save_timers(timers)
            log_action(timer_id, t['name'], action)
            return jsonify(t)
    return jsonify({'error': 'Timer not found'}), 404

# --- Delete Endpoint ---
@app.route('/api/timers/<timer_id>', methods=['DELETE'])
def delete_timer(timer_id):
    timers = load_timers()
    new_list = [t for t in timers if t['id'] != timer_id]
    save_timers(new_list)
    return ('', 204)

# --- CSV Upload Endpoint ---
@app.route('/upload', methods=['POST'])
def upload_csv():
    file = request.files.get('file')
    if file and file.filename.endswith('.csv'):
        file.save(LOG_FILE)
    ensure_log_header()
    return ('', 204)

# --- Statistics Endpoints ---
@app.route('/api/stats', methods=['GET'])
def api_stats():
    ensure_log_header()
    stats = {}
    starts = {}
    today = datetime.date.today().isoformat()
    with open(LOG_FILE, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = dt.fromisoformat(row['timestamp'])
            except Exception:
                continue
            date = ts.date().isoformat()
            tid = row['timer_id']
            name = row['timer_name']
            action = row['action']
            if action == 'start':
                starts[tid] = ts
            elif action == 'stop':
                start = starts.pop(tid, None)
                if start and date == today:
                    elapsed = (ts - start).total_seconds()
                    stats[name] = stats.get(name, 0) + elapsed
    result = [{'name': k, 'hours': round(v/3600, 2)} for k, v in stats.items()]
    return jsonify(result)

# --- Frontend & CSV Download ---
@app.route('/stats', methods=['GET'])
def stats_page():
    return render_template('stats.html')

@app.route('/download')
def download_csv():
    ensure_log_header()
    return send_file(
        LOG_FILE,
        mimetype='text/csv',
        as_attachment=True,
        download_name='timelog.csv'
    )

@app.route('/')
def home():
    return render_template('index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=443, debug=True)
