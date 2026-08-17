#!/usr/bin/env bash

set -euo pipefail

device="${1:-/dev/ttyACM0}"
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
temporary_dir=$(mktemp -d)
logger_was_stopped=0
measurement_interval_seconds=2
minimum_free_bytes=$((128 * 1024))

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

format_duration() {
    total_seconds=$1
    if ((total_seconds < 0)); then
        total_seconds=0
    fi

    days=$((total_seconds / 86400))
    hours=$(((total_seconds % 86400) / 3600))
    minutes=$(((total_seconds % 3600) / 60))
    seconds=$((total_seconds % 60))

    if ((days > 0)); then
        printf '%dd %dh %dm %ds' "$days" "$hours" "$minutes" "$seconds"
    elif ((hours > 0)); then
        printf '%dh %dm %ds' "$hours" "$minutes" "$seconds"
    elif ((minutes > 0)); then
        printf '%dm %ds' "$minutes" "$seconds"
    else
        printf '%ds' "$seconds"
    fi
}

restart_logger() {
    exit_status=$?
    trap - EXIT HUP INT TERM

    rm -rf -- "$temporary_dir"

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
storage_stats=$(
    mpremote connect "$device" exec \
        "import os; s = os.statvfs('/'); print(s[0] * s[2], s[0] * s[3])" \
        | tr -d '\r' \
        | tail -n 1
)
read -r filesystem_total_bytes filesystem_free_bytes <<< "$storage_stats"

if [[ ! $filesystem_total_bytes =~ ^[0-9]+$ || ! $filesystem_free_bytes =~ ^[0-9]+$ ]]; then
    printf 'Error: could not read Pico filesystem capacity.\n' >&2
    exit 1
fi

mapfile -t remote_files < <(
    printf '%s\n' "$listing" \
        | awk '$2 ~ /^gps_[0-9]{8}_[0-9]{6}(_[0-9]{3})?\.csv$/ { print $2 }' \
        | sort
)

if [[ ${#remote_files[@]} -eq 0 ]]; then
    printf 'No GPS log files were found on the Pico.\n' >&2
    exit 1
fi

local_files=()
for remote_file in "${remote_files[@]}"; do
    local_file="$temporary_dir/$remote_file"
    mpremote connect "$device" fs cp ":$remote_file" "$local_file" >/dev/null
    local_files+=("$local_file")
done

timestamp=$(date +%Y%m%d_%H%M%S)
output="$script_dir/gps_export_$timestamp.csv"
suffix=0
while [[ -e $output ]]; do
    suffix=$((suffix + 1))
    printf -v output '%s/gps_export_%s_%03d.csv' "$script_dir" "$timestamp" "$suffix"
done

partial_output="$temporary_dir/merged.csv"
awk -F ',' -v OFS=',' '
    BEGIN {
        print "utc,latitude,longitude,altitude_m,speed_kmh,satellites,hdop"
    }
    FNR == 1 { next }
    NF == 7 && $1 ~ /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$/ {
        if ($5 != "") {
            $5 = sprintf("%.2f", $5 * 1.852)
        }
        print $1, $2, $3, $4, $5, $6, $7
    }
' "${local_files[@]}" > "$partial_output"

mv -- "$partial_output" "$output"
row_count=$(awk 'END { print (NR > 0 ? NR - 1 : 0) }' "$output")

printf '\nGPS session summary (all times are UTC)\n'
printf '=======================================\n'

total_measurements=0
total_log_bytes=0
total_data_bytes=0
session_number=0

for index in "${!remote_files[@]}"; do
    remote_file=${remote_files[$index]}
    local_file=${local_files[$index]}
    file_size=$(stat -c '%s' "$local_file")
    session_stats=$(
        awk -F ',' '
            FNR > 1 && NF == 7 && $1 ~ /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$/ {
                if (measurements == 0) {
                    first = $1
                }
                last = $1
                measurements++
                data_bytes += length($0) + 1
            }
            END {
                printf "%d|%s|%s|%d\n", measurements, first, last, data_bytes
            }
        ' "$local_file"
    )
    IFS='|' read -r measurements first_timestamp last_timestamp data_bytes \
        <<< "$session_stats"

    session_number=$((session_number + 1))
    total_measurements=$((total_measurements + measurements))
    total_log_bytes=$((total_log_bytes + file_size))
    total_data_bytes=$((total_data_bytes + data_bytes))

    printf '\nSession %d: %s\n' "$session_number" "$remote_file"
    if ((measurements > 0)); then
        first_epoch=$(date -u -d "$first_timestamp" '+%s')
        last_epoch=$(date -u -d "$last_timestamp" '+%s')
        session_duration=$((last_epoch - first_epoch))
        first_human=$(date -u -d "$first_timestamp" '+%Y-%m-%d %H:%M:%S UTC')
        last_human=$(date -u -d "$last_timestamp" '+%Y-%m-%d %H:%M:%S UTC')

        printf '  From:         %s\n' "$first_human"
        printf '  To:           %s\n' "$last_human"
        printf '  Measurements: %d\n' "$measurements"
        printf '  Recorded span: %s\n' "$(format_duration "$session_duration")"
    else
        printf '  Measurements: 0 (no valid completed records)\n'
        printf '  Recorded span: n/a\n'
    fi
    printf '  Pico storage: %s\n' "$(format_bytes "$file_size")"
done

filesystem_used_bytes=$((filesystem_total_bytes - filesystem_free_bytes))
usable_free_bytes=$((filesystem_free_bytes - minimum_free_bytes))
if ((usable_free_bytes < 0)); then
    usable_free_bytes=0
fi
free_percentage=$(
    awk -v free="$filesystem_free_bytes" -v total="$filesystem_total_bytes" \
        'BEGIN { printf "%.1f", total ? (free * 100 / total) : 0 }'
)

printf '\nExport summary\n'
printf '==============\n'
printf '  Local file:        %s\n' "$output"
printf '  Session files:     %d\n' "${#remote_files[@]}"
printf '  Measurements:      %d\n' "$row_count"
printf '  Combined Pico logs: %s\n' "$(format_bytes "$total_log_bytes")"

printf '\nPico filesystem\n'
printf '===============\n'
printf '  Total:             %s\n' "$(format_bytes "$filesystem_total_bytes")"
printf '  Used:              %s\n' "$(format_bytes "$filesystem_used_bytes")"
printf '  Free:              %s (%s%%)\n' \
    "$(format_bytes "$filesystem_free_bytes")" "$free_percentage"
printf '  Logger reserve:    %s\n' "$(format_bytes "$minimum_free_bytes")"
printf '  Usable for logs:   %s\n' "$(format_bytes "$usable_free_bytes")"

if ((total_measurements > 0 && total_data_bytes > 0)); then
    average_row_bytes=$(
        awk -v bytes="$total_data_bytes" -v rows="$total_measurements" \
            'BEGIN { printf "%.1f", bytes / rows }'
    )
    estimated_measurements=$((usable_free_bytes * total_measurements / total_data_bytes))
    estimated_seconds=$((estimated_measurements * measurement_interval_seconds))

    printf '\nRemaining-time estimate\n'
    printf '=======================\n'
    printf '  Measurement interval: %ds\n' "$measurement_interval_seconds"
    printf '  Average record size:  %s bytes\n' "$average_row_bytes"
    printf '  Additional records:   approximately %d\n' "$estimated_measurements"
    printf '  Additional duration:  approximately %s\n' \
        "$(format_duration "$estimated_seconds")"
    printf '  (Estimate uses the current average record size and preserves the logger reserve.)\n'
fi

printf '\nThe source files remain on the Pico; logging is restarting now.\n'
