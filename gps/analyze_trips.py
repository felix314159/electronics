#!/usr/bin/env python3
"""Analyze GPS logger CSV files and create an interactive trip overview.

The script intentionally uses only Python's standard library.  The generated HTML
uses the locally installed MapLibre renderer and local vector tiles.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

# WGS84 reference ellipsoid constants
WGS84_A = 6_378_137.0 # semi-major axis (equatorial radius in meters)
WGS84_F = 1 / 298.257223563 # flattening f (earth is not perfectly round)

KNOTS_TO_KMH = 1.852
DEFAULT_SESSION_NAMES = ("Outbound Trip", "Return Trip")

TEMPLATE_PATH = Path(__file__).with_name("template.html")


@dataclass(frozen=True)
class Point:
    timestamp: datetime
    latitude: float
    longitude: float
    altitude_m: float | None
    speed_kmh: float | None
    satellites: int | None
    hdop: float | None


def parse_optional(row: dict[str, str], key: str, conversion=float):
    value = row.get(key, "").strip()
    if not value:
        return None
    try:
        return conversion(value)
    except ValueError:
        return None


def parse_timestamp(value: str) -> datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def load_points(path: Path) -> list[Point]:
    points: list[Point] = []
    with path.open(newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        required = {"utc", "latitude", "longitude"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = sorted(required.difference(reader.fieldnames or []))
            raise ValueError("missing required CSV column(s): " + ", ".join(missing))

        for line_number, row in enumerate(reader, start=2):
            try:
                timestamp = parse_timestamp(row["utc"])
                latitude = float(row["latitude"])
                longitude = float(row["longitude"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"invalid GPS data on CSV line {line_number}: {error}") from error

            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise ValueError(f"coordinate out of range on CSV line {line_number}")

            speed_kmh = parse_optional(row, "speed_kmh")
            if speed_kmh is None:
                speed_knots = parse_optional(row, "speed_knots")
                speed_kmh = None if speed_knots is None else speed_knots * KNOTS_TO_KMH

            points.append(
                Point(
                    timestamp=timestamp,
                    latitude=latitude,
                    longitude=longitude,
                    altitude_m=parse_optional(row, "altitude_m"),
                    speed_kmh=speed_kmh,
                    satellites=parse_optional(row, "satellites", int),
                    hdop=parse_optional(row, "hdop"),
                )
            )

    if not points:
        raise ValueError("the CSV contains no GPS points")
    points.sort(key=lambda point: point.timestamp)
    return points


def split_sessions(points: Sequence[Point], gap_seconds: float) -> list[list[Point]]:
    sessions: list[list[Point]] = [[points[0]]]
    for point in points[1:]:
        gap = (point.timestamp - sessions[-1][-1].timestamp).total_seconds()
        if gap > gap_seconds:
            sessions.append([])
        sessions[-1].append(point)
    return sessions


def haversine_distance_m(first: Point, second: Point) -> float:
    """Fallback spherical distance for coincident/rare non-converging antipodal points."""
    lat1, lat2 = math.radians(first.latitude), math.radians(second.latitude)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(second.longitude - first.longitude)
    h = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 6_371_008.8 * 2 * math.atan2(math.sqrt(h), math.sqrt(max(0.0, 1 - h)))


def geodesic_distance_m(first: Point, second: Point) -> float:
    """Vincenty inverse distance on the WGS-84 ellipsoid."""
    if first.latitude == second.latitude and first.longitude == second.longitude:
        return 0.0

    reduced1 = math.atan((1 - WGS84_F) * math.tan(math.radians(first.latitude)))
    reduced2 = math.atan((1 - WGS84_F) * math.tan(math.radians(second.latitude)))
    sin1, cos1 = math.sin(reduced1), math.cos(reduced1)
    sin2, cos2 = math.sin(reduced2), math.cos(reduced2)
    lon_delta = math.radians(second.longitude - first.longitude)
    lamb = lon_delta

    for _ in range(100):
        sin_lamb, cos_lamb = math.sin(lamb), math.cos(lamb)
        sin_sigma = math.sqrt(
            (cos2 * sin_lamb) ** 2
            + (cos1 * sin2 - sin1 * cos2 * cos_lamb) ** 2
        )
        if sin_sigma == 0:
            return 0.0
        cos_sigma = sin1 * sin2 + cos1 * cos2 * cos_lamb
        sigma = math.atan2(sin_sigma, cos_sigma)
        sin_alpha = cos1 * cos2 * sin_lamb / sin_sigma
        cos_sq_alpha = 1 - sin_alpha**2
        cos_2sigma_m = (
            0.0
            if cos_sq_alpha == 0
            else cos_sigma - 2 * sin1 * sin2 / cos_sq_alpha
        )
        coefficient = (
            WGS84_F
            / 16
            * cos_sq_alpha
            * (4 + WGS84_F * (4 - 3 * cos_sq_alpha))
        )
        next_lamb = lon_delta + (1 - coefficient) * WGS84_F * sin_alpha * (
            sigma
            + coefficient
            * sin_sigma
            * (
                cos_2sigma_m
                + coefficient * cos_sigma * (-1 + 2 * cos_2sigma_m**2)
            )
        )
        if abs(next_lamb - lamb) < 1e-12:
            lamb = next_lamb
            break
        lamb = next_lamb
    else:
        return haversine_distance_m(first, second)

    semi_minor = WGS84_A * (1 - WGS84_F)
    u_sq = cos_sq_alpha * (WGS84_A**2 - semi_minor**2) / semi_minor**2
    big_a = 1 + u_sq / 16384 * (
        4096 + u_sq * (-768 + u_sq * (320 - 175 * u_sq))
    )
    big_b = u_sq / 1024 * (256 + u_sq * (-128 + u_sq * (74 - 47 * u_sq)))
    delta_sigma = big_b * sin_sigma * (
        cos_2sigma_m
        + big_b
        / 4
        * (
            cos_sigma * (-1 + 2 * cos_2sigma_m**2)
            - big_b
            / 6
            * cos_2sigma_m
            * (-3 + 4 * sin_sigma**2)
            * (-3 + 4 * cos_2sigma_m**2)
        )
    )
    return semi_minor * big_a * (sigma - delta_sigma)


def median_smooth(values: Sequence[float], radius: int = 2) -> list[float]:
    return [
        statistics.median(values[max(0, i - radius) : i + radius + 1])
        for i in range(len(values))
    ]


def elevation_gain_loss(values: Sequence[float], deadband_m: float) -> tuple[float, float]:
    """Accumulate only changes that move at least deadband_m from the last extremum."""
    if len(values) < 2:
        return 0.0, 0.0
    ascent = descent = 0.0
    anchor = values[0]
    for value in values[1:]:
        change = value - anchor
        if change >= deadband_m:
            ascent += change
            anchor = value
        elif change <= -deadband_m:
            descent -= change
            anchor = value
    return ascent, descent


def percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def analyze_session(
    points: Sequence[Point],
    number: int,
    name: str,
    moving_threshold_kmh: float,
    altitude_warmup_seconds: float,
    elevation_deadband_m: float,
) -> dict:
    if not points:
        raise ValueError("cannot analyze an empty session")

    segment_distances: list[float] = []
    moving_distance = moving_seconds = speed_integrated_m = 0.0
    for first, second in zip(points, points[1:]):
        seconds = (second.timestamp - first.timestamp).total_seconds()
        distance = geodesic_distance_m(first, second)
        segment_distances.append(distance)
        speeds = [speed for speed in (first.speed_kmh, second.speed_kmh) if speed is not None]
        segment_speed = statistics.mean(speeds) if speeds else (distance / seconds * 3.6 if seconds else 0)
        if seconds > 0:
            speed_integrated_m += segment_speed / 3.6 * seconds
            if segment_speed >= moving_threshold_kmh:
                moving_seconds += seconds
                moving_distance += distance

    elapsed_seconds = max(0.0, (points[-1].timestamp - points[0].timestamp).total_seconds())
    distance_m = sum(segment_distances)
    speeds = [point.speed_kmh for point in points if point.speed_kmh is not None]

    altitude_cutoff = points[0].timestamp.timestamp() + altitude_warmup_seconds
    altitude_points = [
        point
        for point in points
        if point.altitude_m is not None and point.timestamp.timestamp() >= altitude_cutoff
    ]
    altitudes = [point.altitude_m for point in altitude_points if point.altitude_m is not None]
    smoothed_altitudes = median_smooth(altitudes)
    ascent_m, descent_m = elevation_gain_loss(smoothed_altitudes, elevation_deadband_m)

    max_speed_index = max(
        range(len(points)), key=lambda index: points[index].speed_kmh or 0.0
    )
    max_speed_point = points[max_speed_index]
    average_hdop = statistics.mean(
        point.hdop for point in points if point.hdop is not None
    ) if any(point.hdop is not None for point in points) else None

    return {
        "session_number": number,
        "name": name,
        "point_count": len(points),
        "start_utc": points[0].timestamp.isoformat().replace("+00:00", "Z"),
        "end_utc": points[-1].timestamp.isoformat().replace("+00:00", "Z"),
        "elapsed_seconds": elapsed_seconds,
        "moving_seconds": moving_seconds,
        "stopped_seconds": max(0.0, elapsed_seconds - moving_seconds),
        "distance_m": distance_m,
        "speed_integrated_distance_m": speed_integrated_m,
        "average_speed_kmh": distance_m / elapsed_seconds * 3.6 if elapsed_seconds else 0.0,
        "moving_average_speed_kmh": moving_distance / moving_seconds * 3.6 if moving_seconds else 0.0,
        "max_speed_kmh": max(speeds, default=0.0),
        "speed_p95_kmh": percentile(speeds, 0.95),
        "max_speed_location": [max_speed_point.latitude, max_speed_point.longitude],
        "altitude_warmup_seconds": altitude_warmup_seconds,
        "altitude_start_m": smoothed_altitudes[0] if smoothed_altitudes else None,
        "altitude_end_m": smoothed_altitudes[-1] if smoothed_altitudes else None,
        "altitude_min_m": min(smoothed_altitudes, default=None),
        "altitude_max_m": max(smoothed_altitudes, default=None),
        "altitude_net_change_m": (
            smoothed_altitudes[-1] - smoothed_altitudes[0] if smoothed_altitudes else None
        ),
        "total_ascent_m": ascent_m,
        "total_descent_m": descent_m,
        "average_hdop": average_hdop,
        "start_location": [points[0].latitude, points[0].longitude],
        "end_location": [points[-1].latitude, points[-1].longitude],
        "points": [
            {
                "t": point.timestamp.isoformat().replace("+00:00", "Z"),
                "lat": point.latitude,
                "lon": point.longitude,
                "alt": point.altitude_m,
                "speed": point.speed_kmh,
                "sat": point.satellites,
                "hdop": point.hdop,
            }
            for point in points
        ],
    }


def format_duration(seconds: float) -> str:
    seconds = round(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}"


def optional_number(value: float | None, suffix: str = "", decimals: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{decimals}f}{suffix}"


def print_report(reports: Sequence[dict]) -> None:
    for report in reports:
        print(f"\nSession {report['session_number']}: {report['name']}")
        print(f"  UTC:              {report['start_utc']} to {report['end_utc']}")
        print(f"  Track distance:   {report['distance_m'] / 1000:.3f} km")
        print(f"  Elapsed time:     {format_duration(report['elapsed_seconds'])}")
        print(f"  Moving time:      {format_duration(report['moving_seconds'])}")
        print(f"  Average speed:    {report['average_speed_kmh']:.2f} km/h (whole session)")
        print(f"  Moving average:   {report['moving_average_speed_kmh']:.2f} km/h")
        print(f"  Maximum speed:    {report['max_speed_kmh']:.2f} km/h")
        print(
            "  Altitude:         "
            f"{optional_number(report['altitude_start_m'], ' m')} start, "
            f"{optional_number(report['altitude_end_m'], ' m')} end, "
            f"{optional_number(report['altitude_min_m'], ' m')} min, "
            f"{optional_number(report['altitude_max_m'], ' m')} max"
        )
        print(
            f"  Elevation change: +{report['total_ascent_m']:.1f} m / "
            f"-{report['total_descent_m']:.1f} m; "
            f"net {optional_number(report['altitude_net_change_m'], ' m')}"
        )

    if len(reports) > 1:
        total_distance = sum(report["distance_m"] for report in reports)
        total_elapsed = sum(report["elapsed_seconds"] for report in reports)
        print("\nCombined")
        print(f"  Track distance:   {total_distance / 1000:.3f} km")
        print(f"  Elapsed time:     {format_duration(total_elapsed)}")
        print(f"  Maximum speed:    {max(report['max_speed_kmh'] for report in reports):.2f} km/h")
        print(f"  Total ascent:     {sum(report['total_ascent_m'] for report in reports):.1f} m")
        print(f"  Total descent:    {sum(report['total_descent_m'] for report in reports):.1f} m")


def write_map(path: Path, reports: Sequence[dict]) -> None:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    if template.count("__TRIP_DATA__") != 1:
        raise ValueError(
            f"{TEMPLATE_PATH.name} must contain exactly one __TRIP_DATA__ placeholder"
        )
    data = json.dumps(reports, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    path.write_text(template.replace("__TRIP_DATA__", data), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze every GPS CSV session and generate an offline interactive map."
    )
    parser.add_argument("csv_file", type=Path, help="GPS CSV export to analyze")
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("gps_trip_overview.html"),
        help="map HTML output (default: gps_trip_overview.html)",
    )
    parser.add_argument(
        "--gap-minutes", type=float, default=15.0,
        help="start a new session after this gap (default: 15)",
    )
    parser.add_argument(
        "--names", nargs="*", default=list(DEFAULT_SESSION_NAMES),
        help="display names for retained sessions",
    )
    parser.add_argument(
        "--moving-threshold", type=float, default=3.0, metavar="KM/H",
        help="minimum speed counted as moving (default: 3.0 km/h)",
    )
    parser.add_argument(
        "--altitude-warmup", type=float, default=30.0, metavar="SECONDS",
        help="ignore initial altitude acquisition period (default: 30 seconds)",
    )
    parser.add_argument(
        "--elevation-deadband", type=float, default=3.0, metavar="METERS",
        help="altitude change needed before ascent/descent is counted (default: 3 m)",
    )
    parser.add_argument("--json", type=Path, help="also write machine-readable summary JSON")
    parser.add_argument("--no-map", action="store_true", help="do not write the HTML map")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.gap_minutes <= 0:
        parser.error("--gap-minutes must be positive")
    if args.moving_threshold < 0 or args.altitude_warmup < 0 or args.elevation_deadband < 0:
        parser.error("thresholds and warmup duration cannot be negative")

    try:
        all_sessions = split_sessions(load_points(args.csv_file), args.gap_minutes * 60)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    reports = []
    for index, points in enumerate(all_sessions):
        session_number = index + 1
        name = args.names[index] if index < len(args.names) else f"Session {session_number}"
        reports.append(
            analyze_session(
                points,
                session_number,
                name,
                args.moving_threshold,
                args.altitude_warmup,
                args.elevation_deadband,
            )
        )

    print_report(reports)
    if args.json:
        summary = [{key: value for key, value in report.items() if key != "points"} for report in reports]
        args.json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"\nJSON summary: {args.json.resolve()}")
    if not args.no_map:
        try:
            write_map(args.output, reports)
        except (OSError, ValueError) as error:
            parser.error(f"could not create map: {error}")
        print(f"\nInteractive map: {args.output.resolve()}")
        print("View it with:     ./serve_trip_map.py")
    print("\nNote: GPS precision limits the physical accuracy; 'track distance' is the WGS-84")
    print("sum between recorded fixes, not an assertion of millimetre-level ground truth.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
