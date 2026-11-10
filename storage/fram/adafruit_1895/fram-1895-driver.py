# SPDX-FileCopyrightText: 2018 Michael Schroeder for Adafruit Industries
# SPDX-FileCopyrightText: 2026 Felix314159
# SPDX-License-Identifier: MIT

"""MicroPython driver for the Adafruit 1895 I2C FRAM breakout.

The board contains an MB85RC256V: 32 KiB of byte-addressable FRAM at a
default 7-bit I2C address of 0x50.  This module has no third-party
dependencies.  It uses only MicroPython's built-in ``machine`` and ``time``
modules when the relevant convenience helpers are called.

The API is based on Adafruit_CircuitPython_FRAM, but uses chunked sequential
transfers and verifies writes by default.  Verification catches bus errors,
wrong addressing, an asserted WP pin, and read-back mismatches before a write
is reported as successful.
"""

SIZE = 32768
DEFAULT_ADDRESS = 0x50
MANUFACTURER_ID = 0x00A
PRODUCT_ID = 0x510

_DEVICE_ID_ADDRESS = 0x7C  # 7-bit form of the reserved 0xF8 slave ID.
_CRC32_POLYNOMIAL = 0xEDB88320


class FRAMVerificationError(OSError):
    """Raised when bytes read after a write do not match the source data."""


def crc32(data, initial=0):
    """Return a standard CRC-32 of *data* without requiring ``binascii``.

    CRC-32 detects accidental corruption when a value is read again later.
    Read-after-write verification and a stored CRC solve different problems;
    use both for important persistent records.
    """
    crc = (initial ^ 0xFFFFFFFF) & 0xFFFFFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ _CRC32_POLYNOMIAL
            else:
                crc >>= 1
    return (crc ^ 0xFFFFFFFF) & 0xFFFFFFFF


def ticks_us():
    """Return MicroPython's wrapping microsecond counter."""
    import time

    return time.ticks_us()


def ticks_diff(new, old):
    """Return the wrap-safe difference between two ``ticks_us`` values."""
    import time

    return time.ticks_diff(new, old)


class FRAM1895:
    """Adafruit 1895 / MB85RC256V I2C FRAM.

    Args:
        i2c: An initialized MicroPython ``machine.I2C`` or ``SoftI2C`` object.
        address: 7-bit memory address, 0x50 through 0x57 depending on A0-A2.
        wp_pin: Optional initialized Pin object, or a GPIO number.  Connect it
            to the breakout's WP pin to obtain hardware write protection.
        write_protect: Initial software and hardware protection state.
        verify_writes: Read every write back and compare it (default True).
        chunk_size: Maximum data bytes in each I2C transaction.
        retries: Number of retries after the first failed transaction.
        check_device: Read and validate the MB85RC256V device ID at startup.
    """

    def __init__(
        self,
        i2c,
        address=DEFAULT_ADDRESS,
        wp_pin=None,
        write_protect=False,
        verify_writes=True,
        chunk_size=128,
        retries=2,
        check_device=True,
    ):
        if not 0x50 <= address <= 0x57:
            raise ValueError("FRAM address must be in the range 0x50..0x57")
        if not isinstance(chunk_size, int) or chunk_size < 1:
            raise ValueError("chunk_size must be a positive integer")
        if not isinstance(retries, int) or retries < 0:
            raise ValueError("retries must be a non-negative integer")
        if not isinstance(write_protect, bool):
            raise ValueError("write_protect must be True or False")
        if not isinstance(verify_writes, bool):
            raise ValueError("verify_writes must be True or False")

        self.i2c = i2c
        self.address = address
        self.chunk_size = chunk_size
        self.retries = retries
        self.verify_writes = verify_writes
        self._address_buffer = bytearray(2)
        self._write_buffer = bytearray(chunk_size + 2)
        self._verify_buffer = bytearray(chunk_size)
        self._wp_pin = self._prepare_wp_pin(wp_pin, write_protect)
        self._write_protected = write_protect

        if check_device:
            manufacturer, product = self.read_device_id()
            if manufacturer != MANUFACTURER_ID or product != PRODUCT_ID:
                raise OSError(
                    "Unexpected FRAM device ID: manufacturer=0x%03X, "
                    "product=0x%03X" % (manufacturer, product)
                )

    @classmethod
    def for_pico(
        cls,
        i2c_id=0,
        sda_pin=4,
        scl_pin=5,
        frequency=400000,
        wp_pin=None,
        **kwargs
    ):
        """Create the driver and I2C bus for a Pico/Pico 2/Pico 2 W.

        Defaults select I2C0 on GP4 (SDA) and GP5 (SCL).  The MB85RC256V is
        rated to 1 MHz, but 400 kHz gives more margin on breadboard wiring.
        """
        from machine import I2C, Pin

        i2c = I2C(
            i2c_id,
            sda=Pin(sda_pin),
            scl=Pin(scl_pin),
            freq=frequency,
        )
        return cls(i2c, wp_pin=wp_pin, **kwargs)

    @staticmethod
    def _prepare_wp_pin(wp_pin, initial_value):
        if wp_pin is None:
            return None
        if isinstance(wp_pin, int):
            from machine import Pin

            return Pin(wp_pin, Pin.OUT, value=1 if initial_value else 0)
        # Accept an existing machine.Pin-like object, which also makes the
        # class straightforward to test on a host.
        if hasattr(wp_pin, "init"):
            try:
                from machine import Pin

                wp_pin.init(Pin.OUT, value=1 if initial_value else 0)
            except ImportError:
                wp_pin.value(1 if initial_value else 0)
        else:
            wp_pin.value(1 if initial_value else 0)
        return wp_pin

    def __len__(self):
        return SIZE

    @property
    def write_protected(self):
        """True when this instance refuses writes and drives WP high."""
        return self._write_protected

    @write_protected.setter
    def write_protected(self, value):
        if not isinstance(value, bool):
            raise ValueError("write_protected must be True or False")
        if self._wp_pin is not None:
            self._wp_pin.value(1 if value else 0)
        self._write_protected = value

    def read_device_id(self):
        """Return ``(manufacturer_id, product_id)`` from the chip."""
        raw = bytearray(3)
        selected_device = bytes((self.address << 1,))
        last_error = None
        for _ in range(self.retries + 1):
            try:
                # Native MicroPython I2C methods expose ``stop`` as a
                # positional-only argument on RP2 ports.
                count = self.i2c.writeto(
                    _DEVICE_ID_ADDRESS, selected_device, False
                )
                if count is not None and count != 1:
                    raise OSError("Incomplete FRAM device-ID selection")
                self.i2c.readfrom_into(_DEVICE_ID_ADDRESS, raw)
                manufacturer = (raw[0] << 4) | (raw[1] >> 4)
                product = ((raw[1] & 0x0F) << 8) | raw[2]
                return manufacturer, product
            except OSError as error:
                last_error = error
        raise last_error

    @staticmethod
    def _check_range(address, length):
        if not isinstance(address, int):
            raise TypeError("address must be an integer")
        if not isinstance(length, int):
            raise TypeError("length must be an integer")
        if address < 0 or length < 0 or address + length > SIZE:
            raise ValueError(
                "FRAM range 0x%04X + %d is outside 0x0000..0x7FFF"
                % (address, length)
            )

    def _set_address_buffer(self, address):
        self._address_buffer[0] = (address >> 8) & 0x7F
        self._address_buffer[1] = address & 0xFF

    def _read_chunk_into(self, address, buffer):
        self._set_address_buffer(address)
        last_error = None
        for _ in range(self.retries + 1):
            try:
                count = self.i2c.writeto(
                    self.address, self._address_buffer, False
                )
                if count is not None and count != 2:
                    raise OSError("Incomplete FRAM address write")
                self.i2c.readfrom_into(self.address, buffer)
                return
            except OSError as error:
                last_error = error
        raise last_error

    def readinto(self, address, buffer):
        """Read FRAM bytes into a writable buffer and return that buffer."""
        length = len(buffer)
        self._check_range(address, length)
        view = memoryview(buffer)
        offset = 0
        while offset < length:
            count = min(self.chunk_size, length - offset)
            self._read_chunk_into(address + offset, view[offset : offset + count])
            offset += count
        return buffer

    def read(self, address, length=1):
        """Read *length* bytes starting at *address* as a new bytearray."""
        self._check_range(address, length)
        result = bytearray(length)
        return self.readinto(address, result)

    def read_all(self):
        """Read and return all 32 KiB as one bytearray.

        This is convenient for backups and inspection, but it allocates a
        32768-byte result.  Use ``read()`` or ``readinto()`` when RAM usage
        matters.
        """
        return self.read(0, SIZE)

    def _copy_write_chunk(self, address, data, offset, count):
        self._write_buffer[0] = (address >> 8) & 0x7F
        self._write_buffer[1] = address & 0xFF
        for index in range(count):
            value = data[offset + index]
            if not isinstance(value, int) or not 0 <= value <= 255:
                raise ValueError("Every data value must be an integer from 0 to 255")
            self._write_buffer[index + 2] = value

    def _write_chunk(self, address, data, offset, count, verify):
        self._copy_write_chunk(address, data, offset, count)
        packet = memoryview(self._write_buffer)[: count + 2]
        expected = memoryview(self._write_buffer)[2 : count + 2]
        last_error = None

        for _ in range(self.retries + 1):
            try:
                written = self.i2c.writeto(self.address, packet)
                if written is not None and written != count + 2:
                    raise OSError("Incomplete FRAM data write")
                if verify:
                    actual = memoryview(self._verify_buffer)[:count]
                    self._read_chunk_into(address, actual)
                    matches = True
                    for index in range(count):
                        if actual[index] != expected[index]:
                            matches = False
                            break
                    if not matches:
                        last_error = FRAMVerificationError(
                            "FRAM verification failed at 0x%04X" % address
                        )
                        continue
                return
            except OSError as error:
                last_error = error
        raise last_error

    def write(self, address, data, verify=None):
        """Write bytes and return their count.

        Writes are read back and compared unless ``verify=False`` is passed or
        ``verify_writes`` was disabled at construction.  Bus errors and
        verification mismatches are retried.  The operation never wraps from
        address 0x7FFF to 0x0000.
        """
        if self.write_protected:
            raise RuntimeError("FRAM is write protected")
        if isinstance(data, int):
            if not 0 <= data <= 255:
                raise ValueError("Data value must be an integer from 0 to 255")
            data = bytes((data,))
        try:
            length = len(data)
        except TypeError:
            raise TypeError("data must be an integer or a bytes-like sequence")
        self._check_range(address, length)
        if verify is None:
            verify = self.verify_writes
        if not isinstance(verify, bool):
            raise ValueError("verify must be True or False")

        offset = 0
        while offset < length:
            count = min(self.chunk_size, length - offset)
            self._write_chunk(address + offset, data, offset, count, verify)
            offset += count
        return length

    def wipe(self, verify=None):
        """Set every byte in the 32 KiB FRAM to zero and return ``SIZE``.

        FRAM does not require an erase operation; this method explicitly
        writes zero to every address.  Writes are read back and checked by
        default, just like ``write()``.  This irreversibly replaces all stored
        data, so applications should require deliberate user confirmation
        before calling it.
        """
        if self.write_protected:
            raise RuntimeError("FRAM is write protected")
        zeroes = bytearray(self.chunk_size)
        address = 0
        while address < SIZE:
            count = min(self.chunk_size, SIZE - address)
            self.write(address, memoryview(zeroes)[:count], verify=verify)
            address += count
        return SIZE

    def verify(self, address, expected):
        """Return True if FRAM contents exactly equal *expected*."""
        length = len(expected)
        self._check_range(address, length)
        offset = 0
        while offset < length:
            count = min(self.chunk_size, length - offset)
            actual = memoryview(self._verify_buffer)[:count]
            self._read_chunk_into(address + offset, actual)
            for index in range(count):
                if actual[index] != expected[offset + index]:
                    return False
            offset += count
        return True

    def __getitem__(self, key):
        if isinstance(key, int):
            return self.read(key, 1)
        if not isinstance(key, slice):
            raise TypeError("FRAM index must be an integer or slice")
        if key.step not in (None, 1):
            raise ValueError("Slice steps other than 1 are not supported")
        start = 0 if key.start is None else key.start
        stop = SIZE if key.stop is None else key.stop
        if start < 0 or stop < start:
            raise ValueError("Negative or reversed FRAM slices are not supported")
        return self.read(start, stop - start)

    def __setitem__(self, key, value):
        if isinstance(key, int):
            self.write(key, value)
            return
        if not isinstance(key, slice):
            raise TypeError("FRAM index must be an integer or slice")
        if key.step not in (None, 1):
            raise ValueError("Slice steps other than 1 are not supported")
        if key.start is None or key.stop is None:
            raise ValueError("A write slice must have explicit start and stop")
        if key.stop < key.start or len(value) != key.stop - key.start:
            raise ValueError("Slice length must exactly match the data length")
        self.write(key.start, value)


# Familiar alias for users coming from Adafruit_CircuitPython_FRAM.
FRAM_I2C = FRAM1895
