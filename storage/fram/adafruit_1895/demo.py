"""Read/write and integrity demonstration for Adafruit 1895 on Pico 2 W.

This is an intentionally destructive hardware test.  It writes and reads an
example record, reads all 32768 bytes, then wipes and verifies the entire FRAM.
When it finishes successfully, every address from 0x0000 through 0x7FFF is 0.
"""

# The requested filename contains hyphens, so it cannot be used in a normal
# ``import fram_1895_driver`` statement.  MicroPython's import function can
# load it by its exact filename. Sorry about that xD
driver = __import__("fram-1895-driver")


def pack_u32_le(value):
    return bytes(
        (
            value & 0xFF,
            (value >> 8) & 0xFF,
            (value >> 16) & 0xFF,
            (value >> 24) & 0xFF,
        )
    )


def unpack_u32_le(data):
    return data[0] | (data[1] << 8) | (data[2] << 16) | (data[3] << 24)


fram = driver.FRAM1895.for_pico(
    i2c_id=0,
    sda_pin=4,
    scl_pin=5,
    frequency=400000,
    wp_pin=6,
    write_protect=False,
)

manufacturer, product = fram.read_device_id()
print("FRAM detected: manufacturer=0x%03X product=0x%03X" % (manufacturer, product))
print("Capacity: %d bytes" % len(fram))

# Store a small record as: magic + payload length + payload + CRC-32.
#
# ``magic`` is a recognizable signature at the beginning of our record.  A
# reader first checks for b"FRM1" to tell this record format apart from blank,
# corrupt, or unrelated data.  It is not a password and provides no security;
# the CRC below is what detects accidental corruption.
address = 0x0000
magic = b"FRM1"
payload = b"Hello from the Raspberry Pi Pico 2 W!"
header = magic + bytes((len(payload) & 0xFF, (len(payload) >> 8) & 0xFF))
record = header + payload + pack_u32_le(driver.crc32(header + payload))

try:
    # 1. Write the record sequentially from the chosen start address.
    bytes_written = fram.write(address, record)
    end_address = address + bytes_written - 1       # Inclusive last byte.
    next_free_address = address + bytes_written     # First byte after record.
    print(
        "Wrote and verified %d bytes from 0x%04X through 0x%04X."
        % (bytes_written, address, end_address)
    )
    print("Next free address: 0x%04X" % next_free_address)
    print("Record layout:")
    print("  magic   0x%04X..0x%04X  %s" % (address, address + 3, magic))
    print(
        "  length  0x%04X..0x%04X  %d bytes"
        % (address + 4, address + 5, len(payload))
    )
    print(
        "  payload 0x%04X..0x%04X  %s"
        % (address + 6, address + 5 + len(payload), payload)
    )
    print(
        "  CRC-32  0x%04X..0x%04X"
        % (address + 6 + len(payload), end_address)
    )

    # 2. Read just the record back and validate its format, payload and CRC.
    stored_record = fram.read(address, bytes_written)
    stored_header = stored_record[:6]
    if stored_header[:4] != magic:
        raise RuntimeError("Record magic does not match")
    stored_length = stored_header[4] | (stored_header[5] << 8)
    if stored_length != len(payload) or len(stored_record) != stored_length + 10:
        raise RuntimeError("Stored record length is invalid")
    stored_payload = stored_record[6 : 6 + stored_length]
    stored_crc = unpack_u32_le(stored_record[-4:])
    calculated_crc = driver.crc32(stored_record[:-4])
    if stored_payload != payload or stored_crc != calculated_crc:
        raise RuntimeError("Stored record payload or CRC mismatch")
    print("Read record: %s" % bytes(stored_payload))
    print("CRC-32: 0x%08X (valid)" % stored_crc)

    # 3. Read every address and find the same record in the full-memory image.
    all_memory = fram.read_all()
    if len(all_memory) != len(fram):
        raise RuntimeError("Full-memory read returned the wrong size")
    if all_memory[address:next_free_address] != record:
        raise RuntimeError("Record differs in full-memory read")
    print(
        "Read all %d bytes; record at 0x%04X..0x%04X matches."
        % (len(all_memory), address, end_address)
    )
    print("Full-memory CRC-32 before wipe: 0x%08X" % driver.crc32(all_memory))

    # 4. Wipe all addresses.  wipe() verifies each write chunk immediately.
    print("Wiping all addresses 0x0000..0x7FFF to zero...")
    wiped_count = fram.wipe()
    if wiped_count != len(fram):
        raise RuntimeError("FRAM wipe returned the wrong byte count")

    # 5. Read all storage again and independently require every byte to be 0.
    wiped_memory = fram.read_all()
    for wiped_address, value in enumerate(wiped_memory):
        if value != 0:
            raise RuntimeError(
                "FRAM wipe verification failed at 0x%04X" % wiped_address
            )
    print("Read all %d bytes after wipe: every byte is zero." % len(wiped_memory))
    print("Full-memory CRC-32 after wipe: 0x%08X" % driver.crc32(wiped_memory))
    print("Integrity demonstration passed; FRAM is clean and empty.")
finally:
    # Protect the clean memory even if an earlier validation raises an error.
    fram.write_protected = True
    print("Hardware WP is high.")
    # CRC-32 of empty FRAM: 0x011FFCA6