import json
import sqlite3
import logging
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from pprint import pprint

PORT_RECEIVE = 8234
DB_PATH = "datalogger.db"
LOG_PATH = "datalogger.log"
EXPECTED_FIELDS = ("device", "co2_ppm", "temp_c", "humid_rel_perc")
TIMEOUT_SECONDS = 10

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("datalogger")

last_received = time.monotonic()
lock = threading.Lock()

# EXAMPLE SENT BY PICO:
#  {  'co2_ppm':        '569',
#     'device':         'pico-2-w-1',
#     'humid_rel_perc': '22',
#     'temp_c':         '27.24'
#  }

# EXAMPLE LOGGED BY RPI5:
#  {  'co2_ppm':        '569',
#     'device':         'pico-2-w-1',
#     'humid_rel_perc': '22',
#     'received_at':    '2026-04-10T16:44:47.489620',
#     'temp_c':         '27.24'
#  }

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL,
            device TEXT,
            co2_ppm TEXT,
            temp_c TEXT,
            humid_rel_perc TEXT
        )"""
    )
    conn.commit()
    conn.close()


def insert_reading(received_at, data):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO readings (received_at, device, co2_ppm, temp_c, humid_rel_perc) VALUES (?, ?, ?, ?, ?)",
        (received_at, data.get("device"), data.get("co2_ppm"), data.get("temp_c"), data.get("humid_rel_perc")),
    )
    conn.commit()
    conn.close()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        global last_received
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"invalid json")
            return

        missing = [f for f in EXPECTED_FIELDS if not data.get(f)]
        if missing:
            logger.warning("missing or empty fields: %s", ", ".join(missing))

        received_at = datetime.now().isoformat()
        pprint({"received_at": received_at, **data})
        print()
        insert_reading(received_at, data)

        with lock:
            last_received = time.monotonic()

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        pass  # silence default stderr logging


def watchdog():
    while True:
        time.sleep(TIMEOUT_SECONDS)
        with lock:
            elapsed = time.monotonic() - last_received
        if elapsed >= TIMEOUT_SECONDS:
            logger.warning("no data received for %.1f seconds", elapsed)
            received_at = datetime.now().isoformat()
            insert_reading(received_at, {
                "device": "missing",
                "co2_ppm": "0",
                "temp_c": "0",
                "humid_rel_perc": "0",
            })


if __name__ == "__main__":
    from visualizer import start_visualizer

    init_db()
    threading.Thread(target=watchdog, daemon=True).start()
    threading.Thread(target=start_visualizer, daemon=True).start()
    server = HTTPServer(("0.0.0.0", PORT_RECEIVE), Handler)
    print(f"receiver listening on http://localhost:{PORT_RECEIVE}")
    server.serve_forever()
    print()
