# SPDX-License-Identifier: MIT
"""MicroPython driver for Adafruit 5636 / W25Q64JV SPI NOR.

Derived from Adafruit_SPIFlash repo and the Winbond W25Q64JV datasheet.

The block protocol deliberately exposes the native 4096-byte erase sectors.
It is intended for MicroPython VfsLfs2. Writes never erase implicitly:
littlefs must request an erase through ioctl(6) before programming a block.

Integrity policy:
* no chip-erase operation is exposed
* every address and buffer is bounds checked
* protected chips remain usable read-only but all mutations are refused
* illegal NOR 0-to-1 transitions are refused before page programming
* every sector erase and every page program is read back and verified
* all busy waits have timeouts
* formatting requires an explicit confirmation phrase

Note: SPI NOR has no CRC on its SPI link. Read-back verification detects failed
program/erase operations, but applications needing end-to-end detection of
later corruption should also store checksums with their data.
"""

try:
    import uos as os
except ImportError:
    import os

try:
    import vfs
except ImportError:
    # MicroPython releases before the vfs module exposed these APIs via os.
    vfs = os

try:
    import utime as time
except ImportError:
    import time

import machine


class W25Q64:
    """W25Q64JV raw device and littlefs2-compatible block device.

    ``spi`` must be an initialized ``machine.SPI`` object in mode 0 and ``cs``
    must be an output ``machine.Pin``.  The driver does not deinitialize the
    SPI bus because the caller owns it.
    """

    SIZE = 8 * 1024 * 1024
    PAGE_SIZE = 256
    BLOCK_SIZE = 4096
    BLOCK_COUNT = SIZE // BLOCK_SIZE

    JEDEC_W25Q64JV_IQ = b"\xef\x40\x17"
    JEDEC_W25Q64JV_IM = b"\xef\x70\x17"
    FORMAT_CONFIRMATION = "ERASE ALL NOR DATA"

    _CMD_WRITE_ENABLE = 0x06
    _CMD_WRITE_DISABLE = 0x04
    _CMD_READ_STATUS1 = 0x05
    _CMD_READ_STATUS2 = 0x35
    _CMD_READ_STATUS3 = 0x15
    _CMD_READ = 0x03
    _CMD_PAGE_PROGRAM = 0x02
    _CMD_SECTOR_ERASE = 0x20
    _CMD_JEDEC_ID = 0x9F
    _CMD_RELEASE_POWER_DOWN = 0xAB
    _CMD_ENABLE_RESET = 0x66
    _CMD_RESET = 0x99

    _STATUS_BUSY = 0x01
    _STATUS_WEL = 0x02
    _STATUS_BP_MASK = 0x1C
    _STATUS2_CMP = 0x40
    _STATUS3_WPS = 0x04

    def __init__(self, spi, cs, program_timeout_ms=1000,
                 erase_timeout_ms=5000):
        self.spi = spi
        self.cs = cs
        self.program_timeout_ms = int(program_timeout_ms)
        self.erase_timeout_ms = int(erase_timeout_ms)
        if self.program_timeout_ms <= 0 or self.erase_timeout_ms <= 0:
            raise ValueError("timeouts must be positive")

        self._closed = False
        self._header = bytearray(4)
        self._status = bytearray(1)
        self._verify = bytearray(self.PAGE_SIZE)
        self.cs.value(1)

        # Wake a chip left in deep power-down.  Before resetting it, wait for a
        # program/erase that survived a Pico soft reset to finish; aborting that
        # operation here could damage the sector being changed.
        self._command(self._CMD_RELEASE_POWER_DOWN)
        time.sleep_us(5)
        self._wait_ready(self.erase_timeout_ms, "pre-reset operation")
        if self._read_status(self._CMD_READ_STATUS2) & 0x80:
            raise OSError("NOR operation is suspended; refusing to reset it")

        # Return the chip to a known SPI state.  The delays exceed the
        # W25Q64JV tRES1 and software-reset requirements.
        self._command(self._CMD_ENABLE_RESET)
        self._command(self._CMD_RESET)
        time.sleep_us(50)

        first = self._read_jedec_once()
        second = self._read_jedec_once()
        if first != second:
            raise OSError("unstable JEDEC ID: %s then %s" %
                          (self._hex(first), self._hex(second)))
        if first not in (self.JEDEC_W25Q64JV_IQ, self.JEDEC_W25Q64JV_IM):
            raise OSError("expected W25Q64JV JEDEC EF4017/EF7017, got %s" %
                          self._hex(first))
        self.jedec_id = first
        self._wait_ready(self.program_timeout_ms, "initialization")
        self._refresh_protection()

    @staticmethod
    def _hex(data):
        try:
            return data.hex().upper()
        except AttributeError:
            return "".join("%02X" % value for value in data)

    @staticmethod
    def _ticks_ms():
        return time.ticks_ms()

    @staticmethod
    def _ticks_diff(now, before):
        return time.ticks_diff(now, before)

    def _ensure_open(self):
        if self._closed:
            raise OSError("flash device is deinitialized")

    def _select(self):
        self.cs.value(0)

    def _deselect(self):
        self.cs.value(1)

    def _command(self, command):
        self._ensure_open()
        self._status[0] = command
        self._select()
        try:
            self.spi.write(self._status)
        finally:
            self._deselect()

    def _read_command(self, command, destination):
        self._ensure_open()
        self._status[0] = command
        self._select()
        try:
            self.spi.write(self._status)
            self.spi.readinto(destination, 0xFF)
        finally:
            self._deselect()

    def _read_jedec_once(self):
        result = bytearray(3)
        self._read_command(self._CMD_JEDEC_ID, result)
        return bytes(result)

    def _read_status(self, command):
        self._read_command(command, self._status)
        return self._status[0]

    def _wait_ready(self, timeout_ms, operation):
        started = self._ticks_ms()
        while self._read_status(self._CMD_READ_STATUS1) & self._STATUS_BUSY:
            if self._ticks_diff(self._ticks_ms(), started) >= timeout_ms:
                raise OSError("timeout waiting for NOR %s" % operation)
            time.sleep_ms(1)

    def _refresh_protection(self):
        self.status1 = self._read_status(self._CMD_READ_STATUS1)
        self.status2 = self._read_status(self._CMD_READ_STATUS2)
        self.status3 = self._read_status(self._CMD_READ_STATUS3)
        # BP[2:0] or CMP can enable legacy array protection
        self.write_protected = bool(
            (self.status1 & self._STATUS_BP_MASK) or
            (self.status2 & self._STATUS2_CMP) or
            (self.status3 & self._STATUS3_WPS)
        )

    def _require_writable(self):
        self._ensure_open()
        self._refresh_protection()
        if self.write_protected:
            raise OSError(
                "NOR protection is active (SR1=%02X SR2=%02X SR3=%02X); "
                "refusing to modify data" %
                (self.status1, self.status2, self.status3)
            )

    def _write_enable(self):
        self._wait_ready(self.program_timeout_ms, "write-enable")
        self._command(self._CMD_WRITE_ENABLE)
        status = self._read_status(self._CMD_READ_STATUS1)
        if not (status & self._STATUS_WEL):
            raise OSError("NOR did not set its write-enable latch")

    def _set_header(self, command, address):
        self._header[0] = command
        self._header[1] = (address >> 16) & 0xFF
        self._header[2] = (address >> 8) & 0xFF
        self._header[3] = address & 0xFF

    def _check_range(self, address, length):
        self._ensure_open()
        address = int(address)
        length = int(length)
        if address < 0 or length < 0 or address > self.SIZE - length:
            raise ValueError("NOR access outside 0..%d" % (self.SIZE - 1))
        return address, length

    def readinto(self, address, buffer):
        """Read bytes at *address* into a writable buffer."""
        address, length = self._check_range(address, len(buffer))
        if not length:
            return 0
        self._wait_ready(self.program_timeout_ms, "read")
        self._set_header(self._CMD_READ, address)
        self._select()
        try:
            self.spi.write(self._header)
            self.spi.readinto(buffer, 0xFF)
        finally:
            self._deselect()
        return length

    def read(self, address, length):
        """Return *length* bytes beginning at *address*."""
        address, length = self._check_range(address, length)
        result = bytearray(length)
        self.readinto(address, result)
        return bytes(result)

    @staticmethod
    def _equal(left, right, length):
        for index in range(length):
            if left[index] != right[index]:
                return False
        return True

    def _verify_bytes(self, address, expected):
        total = len(expected)
        done = 0
        expected_view = memoryview(expected)
        verify_view = memoryview(self._verify)
        while done < total:
            count = min(self.PAGE_SIZE, total - done)
            target = verify_view[:count]
            self.readinto(address + done, target)
            wanted = expected_view[done:done + count]
            if not self._equal(target, wanted, count):
                for index in range(count):
                    if target[index] != wanted[index]:
                        raise OSError("NOR verify failed at address 0x%06X" %
                                      (address + done + index))
                raise OSError("NOR verify failed")
            done += count

    def _check_programmable(self, address, data):
        """Refuse any write that would silently require a 0-to-1 change."""
        total = len(data)
        done = 0
        data_view = memoryview(data)
        verify_view = memoryview(self._verify)
        while done < total:
            count = min(self.PAGE_SIZE, total - done)
            current = verify_view[:count]
            self.readinto(address + done, current)
            wanted = data_view[done:done + count]
            for index in range(count):
                if (current[index] & wanted[index]) != wanted[index]:
                    raise OSError(
                        "NOR location 0x%06X is not erased; refusing a "
                        "0-to-1 data transition" % (address + done + index)
                    )
            done += count

    def program(self, address, data):
        """Program bytes without erasing and verify all bytes before returning.

        A page program may not cross a 256-byte page boundary, so this method
        splits arbitrary buffers into safe page-sized transactions.
        """
        address, total = self._check_range(address, len(data))
        if not total:
            return 0
        self._require_writable()
        self._check_programmable(address, data)
        view = memoryview(data)
        done = 0
        while done < total:
            page_room = self.PAGE_SIZE - ((address + done) % self.PAGE_SIZE)
            count = min(page_room, total - done)
            chunk = view[done:done + count]
            self._write_enable()
            self._set_header(self._CMD_PAGE_PROGRAM, address + done)
            self._select()
            try:
                self.spi.write(self._header)
                self.spi.write(chunk)
            finally:
                self._deselect()
            self._wait_ready(self.program_timeout_ms, "page program")
            self._verify_bytes(address + done, chunk)
            done += count
        return total

    def _erase_sector(self, block_number):
        block_number = int(block_number)
        if block_number < 0 or block_number >= self.BLOCK_COUNT:
            raise ValueError("NOR erase block outside 0..%d" %
                             (self.BLOCK_COUNT - 1))
        self._require_writable()
        address = block_number * self.BLOCK_SIZE
        self._write_enable()
        self._set_header(self._CMD_SECTOR_ERASE, address)
        self._select()
        try:
            self.spi.write(self._header)
        finally:
            self._deselect()
        self._wait_ready(self.erase_timeout_ms, "sector erase")

        # Verify the entire erase sector, not merely its first page.
        verify_view = memoryview(self._verify)
        for offset in range(0, self.BLOCK_SIZE, self.PAGE_SIZE):
            self.readinto(address + offset, verify_view)
            for index in range(self.PAGE_SIZE):
                if verify_view[index] != 0xFF:
                    raise OSError("NOR erase verify failed at address 0x%06X" %
                                  (address + offset + index))
        return 0

    # MicroPython extended block-device protocol. With the offset argument,
    # writeblocks must not erase: VfsLfs2 performs ioctl(6) separately.
    def readblocks(self, block_number, buffer, offset):
        block_number = int(block_number)
        offset = int(offset)
        if block_number < 0 or offset < 0 or offset >= self.BLOCK_SIZE:
            raise ValueError("invalid NOR block/offset")
        self.readinto(block_number * self.BLOCK_SIZE + offset, buffer)
        return 0

    def writeblocks(self, block_number, buffer, offset):
        block_number = int(block_number)
        offset = int(offset)
        if block_number < 0 or offset < 0 or offset >= self.BLOCK_SIZE:
            raise ValueError("invalid NOR block/offset")
        self.program(block_number * self.BLOCK_SIZE + offset, buffer)
        return 0

    def ioctl(self, operation, argument):
        # MicroPython block-device operations: INIT, DEINIT, SYNC, BLOCK_COUNT,
        # BLOCK_SIZE, BLOCK_ERASE.
        if operation == 1:
            self._ensure_open()
            return 0
        if operation == 2:
            return self.deinit()
        if operation == 3:
            self._wait_ready(self.erase_timeout_ms, "sync")
            return 0
        if operation == 4:
            return self.BLOCK_COUNT
        if operation == 5:
            return self.BLOCK_SIZE
        if operation == 6:
            return self._erase_sector(argument)
        return None

    def format_lfs2(self, confirmation):
        """Create littlefs2 after confirmation; existing files become inaccessible."""
        self._ensure_open()
        if confirmation != self.FORMAT_CONFIRMATION:
            raise ValueError("format refused; pass %r exactly" %
                             self.FORMAT_CONFIRMATION)
        try:
            formatter = vfs.VfsLfs2.mkfs
        except AttributeError:
            raise OSError("this MicroPython build does not include VfsLfs2")
        formatter(self)
        self.ioctl(3, 0)

    def mount_lfs2(self, mount_point="/nor"):
        """Mount an existing littlefs2 filesystem; never formats on failure."""
        self._ensure_open()
        try:
            filesystem = vfs.VfsLfs2(self)
        except AttributeError:
            raise OSError("this MicroPython build does not include VfsLfs2")
        vfs.mount(filesystem, mount_point)
        return filesystem

    def deinit(self):
        if not self._closed:
            self._wait_ready(self.erase_timeout_ms, "deinit")
            self._command(self._CMD_WRITE_DISABLE)
            self.cs.value(1)
            self._closed = True
        return 0

    def info(self):
        return {
            "jedec_id": self._hex(self.jedec_id),
            "size": self.SIZE,
            "page_size": self.PAGE_SIZE,
            "erase_size": self.BLOCK_SIZE,
            "block_count": self.BLOCK_COUNT,
            "write_protected": self.write_protected,
            "status1": self.status1,
            "status2": self.status2,
            "status3": self.status3,
        }


def pico2w_flash(baudrate=12_000_000, spi_id=0, sck=18, mosi=19,
                  miso=16, cs=17):
    """Construct the driver with the documented Pico 2 W default wiring.

    Wiring (Pico -> NOR):

    * 3V3(OUT) -> VIN
    * GND -> GND
    * GP18 -> SCK
    * GP19 -> MOSI
    * GP16 -> MISO
    * GP17 -> CS

    The conservative 12 MHz default favors signal margin on jumper wires.
    """
    cs_pin = machine.Pin(cs, machine.Pin.OUT, value=1)
    spi = machine.SPI(
        spi_id,
        baudrate=int(baudrate),
        polarity=0,
        phase=0,
        bits=8,
        firstbit=machine.SPI.MSB,
        sck=machine.Pin(sck),
        mosi=machine.Pin(mosi),
        miso=machine.Pin(miso),
    )
    return W25Q64(spi, cs_pin)

