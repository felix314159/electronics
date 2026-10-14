# SPDX-License-Identifier: GPL-3.0-or-later
"""Standalone Pico 2 W benchmark. Install the driver as driver.py."""
import gc
import os
import sys
import time
import machine
import micropython
from driver import SDCard


@micropython.viper
def _pattern(buf, seed: uint):
    p = ptr32(buf)
    x = seed | 1
    for i in range(int(len(buf)) // 4):
        x ^= x << 13
        x ^= x >> 17
        x ^= x << 5
        p[i] = x


@micropython.viper
def _stamp(buf, start: uint):
    p = ptr32(buf)
    for i in range(int(len(buf)) // 512):
        p[i * 128] = start + uint(i)
        p[i * 128 + 1] = (start + uint(i)) ^ uint(0xa53cc35a)


def _rate(label, byte_count, elapsed_us):
    # Decimal MB/s: bytes / microseconds. Goal is strictly greater than 10.
    rate = byte_count / max(1, elapsed_us)
    print("%-28s %8.3f MB/s (%d bytes, %d us)" % (label, rate, byte_count, elapsed_us))
    return rate


def _raw(sd, start, total, chunk, passes, write, require_goal):
    n = chunk // 512
    sectors = total // 512
    if start < 0 or start + sectors > sd.sectors or total % chunk:
        raise ValueError("scratch range must fit the card and contain whole chunks")
    buf = bytearray(chunk)
    # Keep readback bounded so large DMA bursts fit the Pico's heap. Every byte
    # is still compared, in smaller slices of the original write buffer.
    verify_size = min(chunk, 16 * 1024)
    verify = bytearray(verify_size) if write else None
    _pattern(buf, 0x31415927)
    view, check_view = memoryview(buf), memoryview(verify) if write else None
    print("Raw range [%d, %d), %d sectors, chunk %d KiB" %
          (start, start + sectors, sectors, chunk // 1024))
    if write:
        if start == 0:
            raise ValueError("refusing raw writes at sector 0; choose an expendable range")
        print("RAW WRITE ENABLED: this entire range is overwritten; card must be unmounted.")
    reads, writes = [], []
    # Untimed warmup read verifies that the starting sector can be received.
    sd.readblocks(start, buf)
    for repeat in range(passes):
        if write:
            _pattern(buf, 0x31415927 ^ repeat)
            gc.collect()
            io_us = 0
            maximum_us = 0
            t_all = time.ticks_us()
            for block in range(start, start + sectors, n):
                _stamp(buf, block)
                t = time.ticks_us()
                sd.writeblocks(block, buf)
                elapsed = time.ticks_diff(time.ticks_us(), t)
                io_us += elapsed
                maximum_us = max(maximum_us, elapsed)
            sd.ioctl(3, 0)
            all_us = time.ticks_diff(time.ticks_us(), t_all)
            wr = _rate("write sustained pass %d" % (repeat + 1), total, all_us)
            _rate("write driver calls only", total, io_us)
            print("Longest write call: %.3f ms" % (maximum_us / 1000))
            writes.append(wr)
            # Full-region readback outside throughput timing. Absolute sector
            # headers detect misaddressing/reordering; the remaining bytes use
            # a deterministic nonuniform pattern, changed every pass.
            for block in range(start, start + sectors, n):
                _stamp(buf, block)
                for off in range(0, chunk, verify_size):
                    size = min(verify_size, chunk - off)
                    target = check_view[:size]
                    expected = view[off:off + size]
                    sd.readblocks(block + off // 512, target)
                    if target != expected:
                        for i in range(size):
                            if target[i] != expected[i]:
                                raise AssertionError("readback mismatch sector %d byte %d" %
                                                     (block + (off + i) // 512,
                                                      (off + i) % 512))
            print("Full raw readback verified.")
            # Exercise single sectors, multiblock and the driver's chunk split.
            # These reads do not change the card or affect timed results.
            for count in (1, 2, 3, min(n, verify_size // 512)):
                for block in (start, start + sectors - count):
                    # Read a whole aligned chunk to form expected data.
                    base = start + ((block - start) // n) * n
                    _stamp(buf, base)
                    off = (block - base) * 512
                    if off + count * 512 <= chunk:
                        sd.readblocks(block, check_view[:count * 512])
                        if check_view[:count * 512] != view[off:off + count * 512]:
                            raise AssertionError("boundary read mismatch")
        gc.collect()
        maximum_us = 0
        t_all = time.ticks_us()
        for block in range(start, start + sectors, n):
            t = time.ticks_us()
            sd.readblocks(block, buf)
            maximum_us = max(maximum_us, time.ticks_diff(time.ticks_us(), t))
        elapsed = time.ticks_diff(time.ticks_us(), t_all)
        reads.append(_rate("read sustained pass %d" % (repeat + 1), total, elapsed))
        print("Longest read call: %.3f ms" % (maximum_us / 1000))
    passed = write and min(reads) > 10 and min(writes) > 10
    print("10 MB/s BOTH DIRECTIONS: " + ("PASS" if passed else "NOT DEMONSTRATED"))
    if require_goal and not passed:
        raise AssertionError("sustained read AND write must each be >10 MB/s in every pass")
    return {"read_MB_s": reads, "write_MB_s": writes, "goal_pass": passed,
            "readback_verified": write, "start_sector": start, "sectors": sectors}


def _files(sd, mount, total, chunk):
    # Measures VfsFat file throughput separately: small filesystem calls may
    # limit performance even when raw DMA bursts meet the goal.
    path = mount + "/sdio-benchmark.bin"
    os.mount(os.VfsFat(sd), mount)
    created = False
    try:
        try:
            os.stat(path)
        except OSError as exc:
            if exc.args[0] != 2:
                raise
        else:
            raise OSError("benchmark file exists; refusing to overwrite " + path)
        stat = os.statvfs(mount)
        if stat[0] * stat[3] < total + chunk:
            raise OSError("not enough free filesystem space")
        data, buf = bytearray(chunk), bytearray(chunk)
        _pattern(data, 0x27182819)
        gc.collect()
        t = time.ticks_us()
        with open(path, "wb") as f:
            created = True
            for _ in range(total // chunk):
                if f.write(data) != chunk:
                    raise OSError("short file write")
            f.flush()
        os.sync()
        _rate("VfsFat file write + sync", total, time.ticks_diff(time.ticks_us(), t))
        t = time.ticks_us()
        with open(path, "rb") as f:
            for _ in range(total // chunk):
                if f.readinto(buf) != chunk:
                    raise OSError("short file read")
        _rate("VfsFat file read", total, time.ticks_diff(time.ticks_us(), t))
        with open(path, "rb") as f:
            for _ in range(total // chunk):
                if f.readinto(buf) != chunk or buf != data:
                    raise AssertionError("file readback mismatch")
        print("Full file readback verified.")
    finally:
        try:
            if created:
                os.remove(path)
                os.sync()
        finally:
            os.umount(mount)


def run(raw_write=False, raw_start=0, total_mib=32, chunk_kib=64, passes=3,
        frequency=37_500_000, max_blocks=128, file_test=False,
        mount="/sd", require_goal=False, **pins):
    if passes < 1 or total_mib < 1 or chunk_kib < 1 or chunk_kib % 1:
        raise ValueError("positive passes/size and integer chunk KiB required")
    total, chunk = int(total_mib * 1024 * 1024), int(chunk_kib * 1024)
    if chunk % 512 or total % chunk:
        raise ValueError("use whole sectors and an integral number of chunks")
    if raw_write and raw_start <= 0:
        raise ValueError("raw_write requires a positive scratch start sector")
    print("Firmware:", sys.version, os.uname())
    print("CPU:", machine.freq(), "Hz; free heap:", gc.mem_free())
    sd = SDCard(frequency=frequency, max_blocks=max_blocks, **pins)
    try:
        print("CID:", sd.cid.hex(), "CSD:", sd.csd.hex())
        print("Capacity:", sd.sectors, "sectors; high speed:", sd.high_speed,
              "; actual SD clock:", sd.frequency, "Hz")
        print("Four-lane wire ceiling:", sd.frequency / 2_000_000, "MB/s before framing/busy")
        result = _raw(sd, raw_start, total, chunk, passes, raw_write, require_goal)
        if file_test:
            _files(sd, mount, total, chunk)
        return result
    finally:
        sd.deinit()


if __name__ == "__main__":
    run()
