"""Integrity-preserving littlefs2 read/write benchmark for Adafruit 5636.

Measures complete filesystem writes including sync and the driver's mandatory erase/program
read-back verification. It never writes raw sectors: a raw 'scratch' sector
could contain live littlefs data. The temporary file is create-only, fully
verified, then removed and synced.
"""

nor = __import__("driver-nor-5636")

MOUNT_POINT = "/nor"
BENCHMARK_FILE = MOUNT_POINT + "/nor-benchmark.tmp"
TOTAL_BYTES = 512 * 1024
CHUNK_BYTES = 4096


def make_pattern(size):
    data = bytearray(size)
    value = 0x5A
    for index in range(size):
        # Deterministic, nonuniform data catches addressing and stale-data bugs.
        value = ((value << 1) ^ (0x1D if value & 0x80 else 0)) & 0xFF
        data[index] = value ^ (index & 0xFF)
    return data


def stamp_pattern(data, chunk_number):
    # Unique per-chunk marker detects addressing, ordering, and stale-read bugs.
    data[0] = chunk_number & 0xFF
    data[1] = (chunk_number >> 8) & 0xFF
    data[2] = (chunk_number >> 16) & 0xFF
    data[3] = (chunk_number >> 24) & 0xFF


def rate(label, byte_count, elapsed_us):
    mib_s = byte_count * 1_000_000 / max(1, elapsed_us) / (1024 * 1024)
    print("%-30s %8.3f MiB/s (%d us)" % (label, mib_s, elapsed_us))
    return mib_s


def main():
    if TOTAL_BYTES <= 0 or CHUNK_BYTES <= 0 or TOTAL_BYTES % CHUNK_BYTES:
        raise ValueError("benchmark sizes must be positive integral chunks")

    flash = nor.pico2w_flash()
    mounted = False
    created = False
    try:
        print("NOR:", flash.info())
        flash.mount_lfs2(MOUNT_POINT)
        mounted = True

        try:
            nor.os.stat(BENCHMARK_FILE)
        except OSError as error:
            if not error.args or error.args[0] != 2:
                raise
        else:
            raise OSError("refusing to overwrite existing " + BENCHMARK_FILE)

        filesystem = nor.os.statvfs(MOUNT_POINT)
        free_bytes = filesystem[0] * filesystem[3]
        if free_bytes < TOTAL_BYTES + 2 * CHUNK_BYTES:
            raise OSError("not enough free littlefs space for safe benchmark")

        pattern = make_pattern(CHUNK_BYTES)
        read_buffer = bytearray(CHUNK_BYTES)
        chunks = TOTAL_BYTES // CHUNK_BYTES

        started = nor.time.ticks_us()
        with open(BENCHMARK_FILE, "xb") as output:
            created = True
            for chunk_number in range(chunks):
                stamp_pattern(pattern, chunk_number)
                if output.write(pattern) != CHUNK_BYTES:
                    raise OSError("short benchmark write")
            output.flush()
        nor.os.sync()
        write_us = nor.time.ticks_diff(nor.time.ticks_us(), started)
        write_rate = rate("littlefs write + sync", TOTAL_BYTES, write_us)

        started = nor.time.ticks_us()
        with open(BENCHMARK_FILE, "rb") as source:
            for chunk_number in range(chunks):
                if source.readinto(read_buffer) != CHUNK_BYTES:
                    raise OSError("short benchmark read at chunk %d" % chunk_number)
                stamp_pattern(pattern, chunk_number)
                if read_buffer != pattern:
                    raise OSError("benchmark verification failed at chunk %d" %
                                  chunk_number)
            if source.read(1):
                raise OSError("benchmark file is longer than expected")
        read_us = nor.time.ticks_diff(nor.time.ticks_us(), started)
        read_rate = rate("littlefs read + verify", TOTAL_BYTES, read_us)
        print("Every benchmark byte verified successfully.")
        return {"write_MiB_s": write_rate, "read_MiB_s": read_rate}
    finally:
        try:
            if mounted:
                if created:
                    try:
                        nor.os.remove(BENCHMARK_FILE)
                        nor.os.sync()
                        print("Temporary benchmark file removed and synced.")
                    except OSError as error:
                        print("WARNING: could not remove benchmark file:", error)
                nor.vfs.umount(MOUNT_POINT)
        finally:
            flash.deinit()


if __name__ == "__main__":
    main()

