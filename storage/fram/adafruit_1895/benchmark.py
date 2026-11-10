"""Read/write benchmark for Adafruit 1895 on a Pico 2 W."""

driver = __import__("fram-1895-driver")

TEST_ADDRESS = 0x7000
TEST_SIZES = (1, 16, 64, 256, 1024, 4096)


def kib_per_second(byte_count, elapsed_us):
    if elapsed_us <= 0:
        return 0.0
    return (byte_count * 1000000.0) / (elapsed_us * 1024.0)


def make_pattern(length):
    # Deterministic pattern: every address is exercised and validation is
    # reproducible without importing a random-number module.
    result = bytearray(length)
    state = 0xA5C31E27
    for index in range(length):
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        result[index] = ((state >> 24) ^ index) & 0xFF
    return result


fram = driver.FRAM1895.for_pico(
    i2c_id=0,
    sda_pin=4,
    scl_pin=5,
    frequency=400000,
    wp_pin=6,
    write_protect=False,
    chunk_size=128,
)

manufacturer, product = fram.read_device_id()
print("Adafruit 1895 FRAM benchmark")
print("Device ID: 0x%03X / 0x%03X" % (manufacturer, product))
print("I2C: 400 kHz; transfer chunk: %d bytes" % fram.chunk_size)
print("Scratch region: 0x%04X..0x7FFF" % TEST_ADDRESS)
print("")
print(" bytes | raw write KiB/s | read KiB/s | verified write KiB/s | integrity")
print("-------+-----------------+------------+----------------------+----------")

original = None
try:
    original = fram.read(TEST_ADDRESS, max(TEST_SIZES))

    for size in TEST_SIZES:
        pattern = make_pattern(size)

        # Time the bus write alone.  The immediately following timed read and
        # byte-for-byte/CRC checks validate that this raw write was correct.
        start = driver.ticks_us()
        fram.write(TEST_ADDRESS, pattern, verify=False)
        raw_write_us = driver.ticks_diff(driver.ticks_us(), start)

        start = driver.ticks_us()
        received = fram.read(TEST_ADDRESS, size)
        read_us = driver.ticks_diff(driver.ticks_us(), start)

        if received != pattern or driver.crc32(received) != driver.crc32(pattern):
            raise driver.FRAMVerificationError(
                "Integrity check failed for %d-byte raw transfer" % size
            )

        # Also measure the safe default operation (write plus read-back
        # comparison), which is the rate applications using this driver see.
        start = driver.ticks_us()
        fram.write(TEST_ADDRESS, pattern)
        verified_write_us = driver.ticks_diff(driver.ticks_us(), start)
        if not fram.verify(TEST_ADDRESS, pattern):
            raise driver.FRAMVerificationError(
                "Post-benchmark verification failed for %d bytes" % size
            )

        print(
            "%6d | %15.2f | %10.2f | %20.2f | PASS"
            % (
                size,
                kib_per_second(size, raw_write_us),
                kib_per_second(size, read_us),
                kib_per_second(size, verified_write_us),
            )
        )
finally:
    try:
        if original is not None:
            fram.write_protected = False
            fram.write(TEST_ADDRESS, original)
            if not fram.verify(TEST_ADDRESS, original):
                raise driver.FRAMVerificationError("Could not restore scratch region")
            print("")
            print("Original 4096-byte scratch region restored and verified.")
    finally:
        fram.write_protected = True
        print("Hardware write protection enabled.")
