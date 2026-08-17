"""Portable GPS logger for a Raspberry Pi Pico 2 W running MicroPython."""

import os

from machine import Pin, UART


UART_ID = 0
UART_TX_PIN = 0
UART_RX_PIN = 1
UART_BAUDRATE = 9600

CSV_HEADER = "utc,latitude,longitude,altitude_m,speed_knots,satellites,hdop\n"
MIN_FREE_BYTES = 128 * 1024


class StorageFullError(Exception):
    pass


def _disable_radios():
    """Explicitly keep the Pico 2 W's Wi-Fi and Bluetooth radios off to save battery power."""
    wifi_off = False
    bluetooth_off = False

    try:
        import network

        for interface in (network.STA_IF, network.AP_IF):
            wlan = network.WLAN(interface)
            wlan.active(False)
        wifi_off = not network.WLAN(network.STA_IF).active() and not network.WLAN(
            network.AP_IF
        ).active()
    except (ImportError, AttributeError, OSError):
        pass

    try:
        import bluetooth

        ble = bluetooth.BLE()
        ble.active(False)
        bluetooth_off = not ble.active()
    except (ImportError, AttributeError, OSError):
        pass

    print(
        "Wireless: Wi-Fi %s; Bluetooth %s"
        % ("off" if wifi_off else "unknown", "off" if bluetooth_off else "unknown")
    )


def _nmea_fields(raw_line):
    """Return checksum-verified NMEA fields, or None for an invalid line."""
    try:
        line = raw_line.decode("ascii").strip()
    except (AttributeError, UnicodeError):
        return None

    if not line.startswith("$"):
        return None

    star = line.rfind("*")
    if star < 0 or star + 3 > len(line):
        return None

    body = line[1:star]
    checksum = 0
    for character in body:
        checksum ^= ord(character)

    try:
        expected_checksum = int(line[star + 1 : star + 3], 16)
    except ValueError:
        return None

    if checksum != expected_checksum:
        return None

    return body.split(",")


def _coordinate(raw_coordinate, hemisphere):
    """Convert an NMEA ddmm.mmmm coordinate to signed decimal degrees."""
    if not raw_coordinate or hemisphere not in ("N", "S", "E", "W"):
        return None

    try:
        degrees_and_minutes = float(raw_coordinate)
    except ValueError:
        return None

    degrees = int(degrees_and_minutes // 100)
    minutes = degrees_and_minutes - degrees * 100
    coordinate = degrees + minutes / 60

    if hemisphere in ("S", "W"):
        coordinate = -coordinate

    return coordinate


def _utc_timestamp(raw_date, raw_time):
    """Convert RMC ddmmyy and hhmmss.sss values to an ISO-8601 UTC string."""
    if len(raw_date) < 6 or len(raw_time) < 6:
        return None

    try:
        day = int(raw_date[0:2])
        month = int(raw_date[2:4])
        year = 2000 + int(raw_date[4:6])
        hour = int(raw_time[0:2])
        minute = int(raw_time[2:4])
        second = int(raw_time[4:6])
    except ValueError:
        return None

    if not (
        1 <= day <= 31
        and 1 <= month <= 12
        and 0 <= hour <= 23
        and 0 <= minute <= 59
        and 0 <= second <= 60
    ):
        return None

    timestamp = "%04d-%02d-%02dT%02d:%02d:%02dZ" % (
        year,
        month,
        day,
        hour,
        minute,
        second,
    )
    return timestamp, second


def _parse_rmc(fields):
    """Parse the time, position, and speed from a valid RMC sentence."""
    if len(fields) < 10 or fields[0][-3:] != "RMC" or fields[2] != "A":
        return None

    timestamp = _utc_timestamp(fields[9], fields[1])
    latitude = _coordinate(fields[3], fields[4])
    longitude = _coordinate(fields[5], fields[6])
    if timestamp is None or latitude is None or longitude is None:
        return None

    try:
        speed_knots = float(fields[7]) if fields[7] else None
    except ValueError:
        speed_knots = None

    return {
        "utc": timestamp[0],
        "utc_second": timestamp[1],
        "gga_time": fields[1][0:6],
        "latitude": latitude,
        "longitude": longitude,
        "speed_knots": speed_knots,
    }


def _parse_gga(fields):
    """Parse altitude, satellite count, and HDOP from a valid GGA sentence."""
    if len(fields) < 10 or fields[0][-3:] != "GGA":
        return None

    try:
        fix_quality = int(fields[6]) if fields[6] else 0
    except ValueError:
        return None
    if fix_quality == 0:
        return None

    try:
        altitude_m = float(fields[9]) if fields[9] else None
    except ValueError:
        altitude_m = None
    try:
        satellites = int(fields[7]) if fields[7] else None
    except ValueError:
        satellites = None
    try:
        hdop = float(fields[8]) if fields[8] else None
    except ValueError:
        hdop = None

    return {
        "gga_time": fields[1][0:6],
        "altitude_m": altitude_m,
        "satellites": satellites,
        "hdop": hdop,
    }


def _free_bytes():
    try:
        filesystem = os.statvfs("/")
        return filesystem[0] * filesystem[3]
    except (AttributeError, OSError):
        return None


def _check_free_space():
    available = _free_bytes()
    if available is not None and available < MIN_FREE_BYTES:
        raise StorageFullError("less than %d bytes remain" % MIN_FREE_BYTES)


def _commit(log_file):
    """Push Python and filesystem buffers to flash."""
    log_file.flush()
    sync = getattr(os, "sync", None)
    if sync is not None:
        sync()


def _path_exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _open_session_file(timestamp):
    """Create a new log file without overwriting an earlier session."""
    compact_timestamp = (
        timestamp[0:4]
        + timestamp[5:7]
        + timestamp[8:10]
        + "_"
        + timestamp[11:13]
        + timestamp[14:16]
        + timestamp[17:19]
    )

    for number in range(1000):
        if number == 0:
            path = "/gps_%s.csv" % compact_timestamp
        else:
            path = "/gps_%s_%03d.csv" % (compact_timestamp, number)

        if not _path_exists(path):
            _check_free_space()
            log_file = open(path, "w")
            log_file.write(CSV_HEADER)
            _commit(log_file)
            return path, log_file

    raise OSError("could not choose a unique GPS log filename")


def _format_optional(value, decimal_places):
    if value is None:
        return ""
    return ("%%.%df" % decimal_places) % value


def _write_record(log_file, rmc, gga):
    if gga is None or gga["gga_time"] != rmc["gga_time"]:
        altitude_m = None
        satellites = None
        hdop = None
    else:
        altitude_m = gga["altitude_m"]
        satellites = gga["satellites"]
        hdop = gga["hdop"]

    row = "%s,%.6f,%.6f,%s,%s,%s,%s\n" % (
        rmc["utc"],
        rmc["latitude"],
        rmc["longitude"],
        _format_optional(altitude_m, 1),
        _format_optional(rmc["speed_knots"], 2),
        "" if satellites is None else str(satellites),
        _format_optional(hdop, 2),
    )

    _check_free_space()
    log_file.write(row)
    _commit(log_file)


def run_logger():
    _disable_radios()

    uart = UART(
        UART_ID,
        baudrate=UART_BAUDRATE,
        tx=Pin(UART_TX_PIN),
        rx=Pin(UART_RX_PIN),
        timeout=1200,
        timeout_char=20,
        rxbuf=2048,
    )

    latest_gga = None
    last_logged_timestamp = None
    log_path = None
    log_file = None

    print("Waiting for a valid GPS fix...")

    try:
        while True:
            raw_line = uart.readline()
            if not raw_line:
                continue

            fields = _nmea_fields(raw_line)
            if fields is None or not fields[0]:
                continue

            sentence_type = fields[0][-3:]
            if sentence_type == "GGA":
                parsed_gga = _parse_gga(fields)
                if parsed_gga is not None:
                    latest_gga = parsed_gga
                continue

            if sentence_type != "RMC":
                continue

            rmc = _parse_rmc(fields)
            if rmc is None:
                continue

            # Logging on even UTC seconds gives one record every two seconds.
            if rmc["utc_second"] % 2 != 0:
                continue
            if rmc["utc"] == last_logged_timestamp:
                continue

            if log_file is None:
                log_path, log_file = _open_session_file(rmc["utc"])
                print("Logging to", log_path)

            _write_record(log_file, rmc, latest_gga)
            last_logged_timestamp = rmc["utc"]
            print(rmc["utc"], rmc["latitude"], rmc["longitude"])

    finally:
        if log_file is not None:
            try:
                _commit(log_file)
            except OSError:
                pass
            log_file.close()
        uart.deinit()


def main():
    try:
        run_logger()
    except KeyboardInterrupt:
        print("Logging stopped.")
    except StorageFullError as error:
        print("Logging stopped: storage is full (%s)." % error)
    except OSError as error:
        print("Logging stopped after a storage or UART error:", error)


if __name__ == "__main__":
    main()
