import json
import sqlite3
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT_VISUALIZE = 8235
DB_PATH = "datalogger.db"
HTML_PATH = Path(__file__).parent / "visualizer.html"


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
        # parse ?range=seconds  (0 = all time)
        range_sec = 600
        if "range=" in self.path:
            try:
                range_sec = int(self.path.split("range=")[1].split("&")[0])
            except ValueError:
                pass

        try:
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
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())
            return

        data = []
        for received_at, device, co2, temp, humid in rows:
            missing = device == "missing"
            data.append({
                "t": received_at,
                "co2": None if missing else (float(co2) if co2 else None),
                "temp": None if missing else (float(temp) if temp else None),
                "hum": None if missing else (float(humid) if humid else None),
            })

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        pass


def start_visualizer():
    server = HTTPServer(("0.0.0.0", PORT_VISUALIZE), Handler)
    print(f"visualizer running on http://localhost:{PORT_VISUALIZE}")
    server.serve_forever()


if __name__ == "__main__":
    start_visualizer()
