#!/usr/bin/env python3
"""Serve the trip overview and local MBTiles archive in a browser."""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

TILE_PATH = re.compile(r"^/tiles/(\d+)/(\d+)/(\d+)\.pbf$")
FONT_PATH = re.compile(r"^/fonts/([^/]+)/(\d+-\d+)$")


class TripMapServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, mbtiles_path: Path):
        super().__init__(address, handler)
        self.mbtiles_path = mbtiles_path


class TripMapHandler(SimpleHTTPRequestHandler):
    server: TripMapServer

    def do_GET(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/gps_trip_overview.html")
            self.end_headers()
            return

        tile_match = TILE_PATH.fullmatch(path)
        if tile_match:
            self._serve_tile(*(int(value) for value in tile_match.groups()))
            return

        font_match = FONT_PATH.fullmatch(path)
        if font_match:
            font_name, glyph_range = font_match.groups()
            self._serve_font(font_name, glyph_range)
            return

        super().do_GET()

    def _serve_tile(self, zoom: int, column: int, row: int) -> None:
        if not (0 <= zoom <= 30 and 0 <= column < 2**zoom and 0 <= row < 2**zoom):
            self.send_error(404, "tile outside valid range")
            return

        # Web maps address rows from the north; MBTiles stores TMS rows from the south.
        tms_row = (2**zoom - 1) - row
        uri = f"file:{self.server.mbtiles_path.as_posix()}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True) as database:
                result = database.execute(
                    "SELECT tile_data FROM tiles "
                    "WHERE zoom_level = ? AND tile_column = ? AND tile_row = ?",
                    (zoom, column, tms_row),
                ).fetchone()
        except sqlite3.Error as error:
            self.send_error(500, f"could not read MBTiles archive: {error}")
            return

        if result is None:
            self.send_error(404, "tile not present in the map archive")
            return

        data = result[0]
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.mapbox-vector-tile")
        if data.startswith(b"\x1f\x8b"):
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_font(self, font_name: str, glyph_range: str) -> None:
        fonts_root = Path(self.directory) / "offline_map" / "fonts"
        font_path = (fonts_root / font_name / glyph_range).resolve()
        try:
            font_path.relative_to(fonts_root.resolve())
            data = font_path.read_bytes()
        except (OSError, ValueError):
            self.send_error(404, "font range not installed")
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open the GPS trip overview using the local vector map."
    )
    parser.add_argument("--host", default="127.0.0.1", help="listen address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="listen port (default: 8000)")
    parser.add_argument(
        "--mbtiles",
        type=Path,
        help="MBTiles archive (default: the only .mbtiles file in offline_map)",
    )
    parser.add_argument("--no-browser", action="store_true", help="do not launch the browser")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_dir = Path(__file__).resolve().parent
    if args.mbtiles is None:
        candidates = sorted((project_dir / "offline_map").glob("*.mbtiles"))
        if len(candidates) != 1:
            print(
                "Expected exactly one .mbtiles file in offline_map; "
                "select one with --mbtiles.",
                file=sys.stderr,
            )
            return 1
        mbtiles_path = candidates[0].resolve()
    else:
        mbtiles_path = args.mbtiles.expanduser().resolve()
    required = [
        project_dir / "gps_trip_overview.html",
        project_dir / "offline_map" / "assets" / "maplibre-gl.js",
        project_dir / "offline_map" / "assets" / "maplibre-gl.css",
        mbtiles_path,
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        print("Missing required offline map file(s):", file=sys.stderr)
        for path in missing:
            print(f"  {path}", file=sys.stderr)
        return 1
    if not 1 <= args.port <= 65535:
        print("Port must be between 1 and 65535.", file=sys.stderr)
        return 2

    handler = partial(TripMapHandler, directory=str(project_dir))
    try:
        server = TripMapServer((args.host, args.port), handler, mbtiles_path)
    except OSError as error:
        print(f"Could not start map server: {error}", file=sys.stderr)
        return 1

    url = f"http://{args.host}:{args.port}/gps_trip_overview.html"
    print(f"Offline trip map: {url}")
    print("Press Ctrl+C to stop the local server.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nMap server stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
