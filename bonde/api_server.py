"""
Flask API Server for Stockbee Scanner Dashboard
Run: python api_server.py
"""

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import json
import os
import threading
import time
import datetime

app = Flask(__name__, static_folder=".")
CORS(app)

RESULTS_FILE = "scan_results.json"
_cache = {"results": [], "last_scan": None}

def background_scanner():
    """Run scanner in background thread"""
    from scanner_engine import run_scanner, save_results, is_market_hours
    while True:
        if is_market_hours():
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Running background scan...")
            results = run_scanner(max_symbols=100)
            save_results(results, RESULTS_FILE)
            _cache["results"] = results
            _cache["last_scan"] = datetime.datetime.now().isoformat()
            time.sleep(1800)  # 30 min
        else:
            # Load last results if available
            if os.path.exists(RESULTS_FILE):
                with open(RESULTS_FILE) as f:
                    _cache["results"] = json.load(f)
            time.sleep(300)

@app.route("/api/scan")
def get_scan_results():
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE) as f:
            data = json.load(f)
        return jsonify({
            "status": "ok",
            "count": len(data),
            "last_scan": _cache.get("last_scan", "unknown"),
            "results": data
        })
    return jsonify({"status": "no_data", "results": [], "count": 0})

@app.route("/api/market_status")
def market_status():
    from scanner_engine import is_market_hours
    import pytz
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.datetime.now(ist)
    return jsonify({
        "is_open": is_market_hours(),
        "time_ist": now.strftime("%H:%M:%S"),
        "date": now.strftime("%d %b %Y"),
        "weekday": now.strftime("%A")
    })

@app.route("/api/trigger_scan")
def trigger_scan():
    """Manually trigger a scan"""
    def _scan():
        from scanner_engine import run_scanner, save_results
        r = run_scanner(max_symbols=80)
        save_results(r, RESULTS_FILE)
        _cache["results"] = r
        _cache["last_scan"] = datetime.datetime.now().isoformat()
    t = threading.Thread(target=_scan)
    t.daemon = True
    t.start()
    return jsonify({"status": "scan_triggered", "message": "Scan started in background. Refresh in ~2-3 min."})

@app.route("/")
def index():
    return send_from_directory(".", "dashboard.html")

if __name__ == "__main__":
    # Start background scanner
    t = threading.Thread(target=background_scanner)
    t.daemon = True
    t.start()
    print("\n🚀 Stockbee Scanner API running at http://localhost:5000")
    print("   Dashboard: http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
