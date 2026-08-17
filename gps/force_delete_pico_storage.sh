#!/usr/bin/env bash

set -euo pipefail

device="${1:-/dev/ttyACM0}"
logger_was_stopped=0

format_bytes() {
    awk -v bytes="$1" 'BEGIN {
        if (bytes >= 1048576) {
            printf "%.2f MiB (%d bytes)", bytes / 1048576, bytes
        } else if (bytes >= 1024) {
            printf "%.2f KiB (%d bytes)", bytes / 1024, bytes
        } else {
            printf "%d bytes", bytes
        }
    }'
}

read_free_bytes() {
    mpremote connect "$device" exec \
        "import os; s = os.statvfs('/'); print(s[0] * s[3])" \
        | tr -d '\r' \
        | tail -n 1
}

restart_logger() {
    exit_status=$?
    trap - EXIT HUP INT TERM

    if [[ $logger_was_stopped -eq 1 ]]; then
        if ! mpremote connect "$device" reset >/dev/null 2>&1; then
            printf 'Warning: could not restart the logger on %s. Reconnect USB to restart it.\n' "$device" >&2
        fi
    fi

    exit "$exit_status"
}

trap restart_logger EXIT HUP INT TERM

if ! command -v mpremote >/dev/null 2>&1; then
    printf 'Error: mpremote is not installed or is not in PATH.\n' >&2
    exit 1
fi

if [[ ! -e $device ]]; then
    printf 'Error: Pico serial device %s was not found.\n' "$device" >&2
    exit 1
fi

# Connecting sends Ctrl-C. gps.py catches it and syncs/closes the current log.
logger_was_stopped=1
listing=$(mpremote connect "$device" fs ls :)

mapfile -t remote_files < <(
    printf '%s\n' "$listing" \
        | tr -d '\r' \
        | awk '$2 ~ /^gps_[0-9]{8}_[0-9]{6}(_[0-9]{3})?\.csv$/ { print $2 }' \
        | sort
)

if [[ ${#remote_files[@]} -eq 0 ]]; then
    printf 'No GPS session files were found on the Pico. Nothing was deleted.\n'
    exit 0
fi

log_bytes=$(
    printf '%s\n' "$listing" \
        | tr -d '\r' \
        | awk '$2 ~ /^gps_[0-9]{8}_[0-9]{6}(_[0-9]{3})?\.csv$/ { bytes += $1 } END { print bytes + 0 }'
)
free_before=$(read_free_bytes)

if [[ ! $free_before =~ ^[0-9]+$ ]]; then
    printf 'Error: could not read the Pico free-space value.\n' >&2
    exit 1
fi

printf 'The following %d GPS session file(s) will be permanently deleted:\n' \
    "${#remote_files[@]}"
for remote_file in "${remote_files[@]}"; do
    printf '  %s\n' "$remote_file"
done
printf '\nGPS log storage to delete: %s\n' "$(format_bytes "$log_bytes")"
printf 'Local gps_export_*.csv files will not be touched.\n'
printf 'MicroPython, gps.py, and main.py will remain installed.\n\n'
printf 'Type DELETE to continue: '
read -r confirmation

if [[ $confirmation != DELETE ]]; then
    printf 'Deletion cancelled; no files were removed.\n'
    exit 0
fi

delete_failed=0
for remote_file in "${remote_files[@]}"; do
    if mpremote connect "$device" fs rm ":$remote_file" >/dev/null; then
        printf 'Deleted %s\n' "$remote_file"
    else
        printf 'Error: could not delete %s\n' "$remote_file" >&2
        delete_failed=1
    fi
done

remaining_listing=$(mpremote connect "$device" fs ls :)
mapfile -t remaining_files < <(
    printf '%s\n' "$remaining_listing" \
        | tr -d '\r' \
        | awk '$2 ~ /^gps_[0-9]{8}_[0-9]{6}(_[0-9]{3})?\.csv$/ { print $2 }'
)

if ((delete_failed != 0 || ${#remaining_files[@]} != 0)); then
    printf 'Error: deletion was incomplete; %d GPS session file(s) remain.\n' \
        "${#remaining_files[@]}" >&2
    exit 1
fi

free_after=$(read_free_bytes)
if [[ ! $free_after =~ ^[0-9]+$ ]]; then
    printf 'Error: files were deleted, but the final free-space value could not be read.\n' >&2
    exit 1
fi

reclaimed_bytes=$((free_after - free_before))
if ((reclaimed_bytes < 0)); then
    reclaimed_bytes=0
fi

printf '\nDeletion complete.\n'
printf '  Deleted sessions: %d\n' "${#remote_files[@]}"
printf '  Reclaimed space:  %s\n' "$(format_bytes "$reclaimed_bytes")"
printf '  Pico free space:  %s\n' "$(format_bytes "$free_after")"

# Keep the Pico at the REPL so it does not immediately create another log.
logger_was_stopped=0
printf 'The Pico is now idle and its GPS log storage is empty.\n'
printf 'Reconnect USB, press reset, or power it from batteries to start the next session.\n'
