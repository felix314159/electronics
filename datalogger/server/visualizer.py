import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT_VISUALIZE = 8235
DB_PATH = "datalogger.db"
HTML_PATH = Path(__file__).parent / "visualizer.html"

# range_sec -> (bucket_sec, refresh_interval_sec). None = serve raw rows.
# Buckets sized so each long-range chart has ~1000 points.
RANGE_CONFIG = {
    60:       None,
    600:      None,
    3600:     None,
    86400:    (60,    300),    # 1 day:   1-min buckets, refresh 5 min
    604800:   (600,   1800),   # 1 week:  10-min buckets, refresh 30 min
    2592000:  (3600,  3600),   # 1 month: 1-h buckets,   refresh 1 h
    31536000: (21600, 10800),  # 1 year:  6-h buckets,   refresh 3 h
    0:        (86400, 21600),  # all time:1-d buckets,   refresh 6 h
}

aggregate_cache = {}        # range_sec -> list[dict]
last_refreshed = {}         # range_sec -> monotonic time
cache_lock = threading.Lock()


def ensure_index():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_readings_received_at ON readings(received_at)")
    conn.commit()
    conn.close()


def compute_aggregate(range_sec, bucket_sec):
    # Bucket = floor(unix_seconds / bucket_sec) * bucket_sec.
    # strftime treats the naive ISO string as UTC; utcfromtimestamp reverses
    # that on the way out, so the wire format matches raw received_at.
    sql = (
        "SELECT (CAST(strftime('%s', received_at) AS INTEGER) / ?) * ? AS bucket, "
        "AVG(CAST(co2_ppm AS REAL)), "
        "AVG(CAST(temp_c AS REAL)), "
        "AVG(CAST(humid_rel_perc AS REAL)) "
        "FROM readings WHERE device != 'missing'"
    )
    params = [bucket_sec, bucket_sec]
    if range_sec > 0:
        sql += " AND received_at >= datetime('now', 'localtime', ?||' seconds')"
        params.append(str(-range_sec))
    sql += " GROUP BY bucket ORDER BY bucket"

    conn = sqlite3.connect(DB_PATH, timeout=5)
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    out = []
    for bucket, co2, temp, hum in rows:
        out.append({
            "t": datetime.utcfromtimestamp(int(bucket)).isoformat(),
            "co2": co2,
            "temp": temp,
            "hum": hum,
        })
    return out


def fetch_raw(range_sec):
    conn = sqlite3.connect(DB_PATH, timeout=5)
    if range_sec > 0:
        rows = conn.execute(
            "SELECT received_at, device, co2_ppm, temp_c, humid_rel_perc FROM readings "
            "WHERE received_at >= datetime('now', 'localtime', ?||' seconds') ORDER BY received_at",
            (str(-range_sec),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT received_at, device, co2_ppm, temp_c, humid_rel_perc FROM readings ORDER BY received_at"
        ).fetchall()
    conn.close()

    out = []
    for received_at, device, co2, temp, humid in rows:
        missing = device == "missing"
        out.append({
            "t": received_at,
            "co2": None if missing else (float(co2) if co2 else None),
            "temp": None if missing else (float(temp) if temp else None),
            "hum": None if missing else (float(humid) if humid else None),
        })
    return out


def refresh_if_stale(range_sec, bucket_sec, interval, force=False):
    now = time.monotonic()
    if not force and now - last_refreshed.get(range_sec, 0) < interval:
        return
    try:
        data = compute_aggregate(range_sec, bucket_sec)
    except Exception as e:
        print(f"aggregate refresh failed for range={range_sec}: {e}")
        return
    with cache_lock:
        aggregate_cache[range_sec] = data
        last_refreshed[range_sec] = now


def cache_refresher():
    while True:
        for range_sec, cfg in RANGE_CONFIG.items():
            if cfg is None:
                continue
            bucket_sec, interval = cfg
            refresh_if_stale(range_sec, bucket_sec, interval)
        time.sleep(10)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/readings"):
            self._serve_api()
        else:
            self._serve_html()

    def _serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(HTML_PATH.read_bytes())

    def _serve_api(self):
        range_sec = 600
        if "range=" in self.path:
            try:
                range_sec = int(self.path.split("range=")[1].split("&")[0])
            except ValueError:
                pass

        try:
            cfg = RANGE_CONFIG.get(range_sec)
            if cfg is None:
                data = fetch_raw(range_sec)
            else:
                bucket_sec, interval = cfg
                with cache_lock:
                    data = aggregate_cache.get(range_sec)
                if data is None:
                    refresh_if_stale(range_sec, bucket_sec, interval, force=True)
                    with cache_lock:
                        data = aggregate_cache.get(range_sec, [])
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        pass


def start_visualizer():
    ensure_index()
    for range_sec, cfg in RANGE_CONFIG.items():
        if cfg is None:
            continue
        bucket_sec, interval = cfg
        refresh_if_stale(range_sec, bucket_sec, interval, force=True)
    threading.Thread(target=cache_refresher, daemon=True).start()
    server = HTTPServer(("0.0.0.0", PORT_VISUALIZE), Handler)
    print(f"visualizer running on http://localhost:{PORT_VISUALIZE}")
    server.serve_forever()


if __name__ == "__main__":
    start_visualizer()
