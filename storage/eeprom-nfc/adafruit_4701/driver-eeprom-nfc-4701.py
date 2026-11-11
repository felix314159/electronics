"""MicroPython driver for Adafruit 4701 / ST25DV16K dynamic NFC tag.

The ST25DV16K is a 2 KiB EEPROM with two ways to reach the same memory:

* a microcontroller uses I2C (7-bit addresses 0x53 and 0x57), and
* a phone/reader uses 13.56 MHz ISO/IEC 15693 / NFC Forum Type 5 RF.

This module supports raw EEPROM access, device information, NFC Data Exchange Format (NDEF) URI/text records, RF field
status, and the already-enabled fast-transfer mailbox.
"""

try:
    from time import ticks_ms, ticks_diff, sleep_ms
except ImportError:  # Lets the transport-independent parts run on CPython.
    from time import monotonic, sleep

    def ticks_ms():
        return int(monotonic() * 1000)

    def ticks_diff(new, old):
        return new - old

    def sleep_ms(milliseconds):
        sleep(milliseconds / 1000)


class ST25DVError(Exception):
    """Base exception raised by this driver."""


class ST25DVNotFoundError(ST25DVError):
    """The ST25DV did not answer on both required I2C addresses."""


class NDEFError(ST25DVError):
    """The EEPROM is not formatted as expected or an NDEF record is invalid."""


class ST25DV16K:
    """Adafruit 4701 / ST25DV04K, ST25DV16K, or ST25DV64K I2C driver.

    Despite the class name, memory capacity is read from the chip, so the raw
    memory and NDEF functions also work with other original ST25DVxxK sizes.
    This is for the original K register map used by Adafruit 4701, not a general
    ST25DVxxKC configuration driver.
    """

    USER_ADDRESS = 0x53
    SYSTEM_ADDRESS = 0x57

    REG_MEM_SIZE = 0x0014
    REG_BLOCK_SIZE = 0x0016
    REG_IC_REF = 0x0017
    REG_UID = 0x0018
    REG_IC_REV = 0x0020

    REG_EH_CTRL_DYN = 0x2002
    REG_RF_MNGT_DYN = 0x2003
    REG_I2C_SSO_DYN = 0x2004
    REG_IT_STS_DYN = 0x2005
    REG_MB_CTRL_DYN = 0x2006
    REG_MB_LEN_DYN = 0x2007
    REG_MAILBOX = 0x2008

    MB_ENABLED = 0x01
    MB_HOST_PUT_MSG = 0x02
    MB_RF_PUT_MSG = 0x04

    CC_MAGIC = 0xE1
    CC_MAGIC_EXTENDED = 0xE2
    NDEF_TLV = 0x03
    TERMINATOR_TLV = 0xFE

    # NFC Forum URI RTD prefix compression table.
    URI_PREFIXES = (
        "",
        "http://www.",
        "https://www.",
        "http://",
        "https://",
        "tel:",
        "mailto:",
        "ftp://anonymous:anonymous@",
        "ftp://ftp.",
        "ftps://",
        "sftp://",
        "smb://",
        "nfs://",
        "ftp://",
        "dav://",
        "news:",
        "telnet://",
        "imap:",
        "rtsp://",
        "urn:",
        "pop:",
        "sip:",
        "sips:",
        "tftp:",
        "btspp://",
        "btl2cap://",
        "btgoep://",
        "tcpobex://",
        "irdaobex://",
        "file://",
        "urn:epc:id:",
        "urn:epc:tag:",
        "urn:epc:pat:",
        "urn:epc:raw:",
        "urn:epc:",
        "urn:nfc:",
    )

    def __init__(
        self,
        i2c,
        user_address=USER_ADDRESS,
        system_address=SYSTEM_ADDRESS,
        transfer_size=128,
        write_timeout_ms=400,
        verify=True,
    ):
        self.i2c = i2c
        self.user_address = user_address
        self.system_address = system_address
        self.transfer_size = int(transfer_size)
        self.write_timeout_ms = int(write_timeout_ms)
        if self.transfer_size < 1 or self.transfer_size > 256:
            raise ValueError("transfer_size must be from 1 to 256")

        self._device_info = None
        if verify:
            self._probe()

    @classmethod
    def from_pico(cls, i2c_id=0, sda=4, scl=5, freq=400_000, **kwargs):
        """Create the driver using Pico/Pico 2 W GPIO numbers.

        Defaults are I2C0 on GP4 (SDA) and GP5 (SCL).  ``sda`` and ``scl``
        are GPIO numbers, not physical header pin numbers.
        """
        from machine import I2C, Pin

        bus = I2C(i2c_id, sda=Pin(sda), scl=Pin(scl), freq=freq)
        return cls(bus, **kwargs)

    @staticmethod
    def _address_bytes(address):
        if not 0 <= address <= 0xFFFF:
            raise ValueError("16-bit memory address out of range")
        return bytes((address >> 8, address & 0xFF))

    def _read_at(self, device_address, memory_address, length):
        if length < 0:
            raise ValueError("length cannot be negative")
        result = bytearray(length)
        done = 0
        while done < length:
            count = min(self.transfer_size, length - done)
            pointer = self._address_bytes(memory_address + done)
            # A repeated START is the normal random-read sequence for ST25DV.
            self.i2c.writeto(device_address, pointer, False)
            result[done : done + count] = self.i2c.readfrom(
                device_address, count
            )
            done += count
        return bytes(result)

    def _wait_ready(self, device_address):
        """ACK-poll after an EEPROM programming operation."""
        started = ticks_ms()
        while True:
            try:
                # An address-only write is the ST-recommended ACK poll.
                self.i2c.writeto(device_address, b"")
                return
            except OSError:
                if ticks_diff(ticks_ms(), started) >= self.write_timeout_ms:
                    raise ST25DVError(
                        "timeout waiting for EEPROM programming at I2C 0x%02X"
                        % device_address
                    )
                sleep_ms(1)

    def _write_at(
        self, device_address, memory_address, data, wait_for_eeprom=True
    ):
        data = bytes(data)
        done = 0
        while done < len(data):
            count = min(self.transfer_size, len(data) - done)
            packet = self._address_bytes(memory_address + done) + data[
                done : done + count
            ]
            self.i2c.writeto(device_address, packet)
            if wait_for_eeprom:
                self._wait_ready(device_address)
            done += count

    def _probe(self):
        try:
            # Reading these registers also proves that 0x57 is system memory.
            raw = self._read_at(self.system_address, self.REG_MEM_SIZE, 4)
            # Prove the user/dynamic address separately without altering it.
            self._read_at(self.user_address, self.REG_EH_CTRL_DYN, 1)
        except OSError as exc:
            raise ST25DVNotFoundError(
                "ST25DV not found at I2C user/system addresses 0x%02X/0x%02X"
                % (self.user_address, self.system_address)
            ) from exc

        block_count = raw[0] | (raw[1] << 8)
        self._device_info = {
            "memory_blocks": block_count + 1,
            "block_size": raw[2] + 1,
            "capacity_bytes": (block_count + 1) * (raw[2] + 1),
            "ic_reference": raw[3],
        }

    def scan(self):
        """Return all 7-bit I2C addresses currently visible on the bus."""
        return self.i2c.scan()

    @property
    def capacity(self):
        """User EEPROM capacity in bytes, detected from the system registers."""
        if self._device_info is None:
            self._probe()
        return self._device_info["capacity_bytes"]

    def device_info(self):
        """Return chip identity, capacity, revision, and conventional UID."""
        if self._device_info is None:
            self._probe()
        info = dict(self._device_info)
        info["ic_revision"] = self._read_at(
            self.system_address, self.REG_IC_REV, 1
        )[0]
        info["uid"] = self.uid_hex()

        capacity_names = {
            512: "ST25DV04K",
            2048: "ST25DV16K",
            8192: "ST25DV64K",
        }
        info["model"] = capacity_names.get(
            info["capacity_bytes"], "ST25DV (unknown capacity)"
        )
        return info

    def uid_bytes(self):
        """Return the ISO 15693 UID most-significant byte first (starts E0...)."""
        # Registers 0x18..0x1f store UID byte 0 (LSB) through byte 7 (MSB).
        raw = self._read_at(self.system_address, self.REG_UID, 8)
        # MicroPython does not implement byte slices with a negative step.
        return bytes(
            (raw[7], raw[6], raw[5], raw[4], raw[3], raw[2], raw[1], raw[0])
        )

    def uid_hex(self, separator=":"):
        """Return a conventional, human-readable ISO 15693 UID string."""
        return separator.join("%02X" % value for value in self.uid_bytes())

    def read(self, address, length):
        """Read bytes from the nonvolatile user EEPROM."""
        if address < 0 or length < 0 or address + length > self.capacity:
            raise ValueError("read exceeds the user EEPROM")
        return self._read_at(self.user_address, address, length)

    def write(self, address, data, verify=False):
        """Write bytes to the nonvolatile user EEPROM.

        Raw writes can overwrite the Type 5 Capability Container or an NDEF
        message.  Prefer ``write_ndef_uri``/``write_ndef_text`` for phone-readable
        data.  ``verify=True`` reads the bytes back and raises on a mismatch.
        """
        data = bytes(data)
        if address < 0 or address + len(data) > self.capacity:
            raise ValueError("write exceeds the user EEPROM")
        self._write_at(self.user_address, address, data)
        if verify and self.read(address, len(data)) != data:
            raise ST25DVError("EEPROM read-back verification failed")

    def _read_cc(self):
        cc = self.read(0, 4)
        if (cc[1] & 0xFC) != 0x40:
            raise NDEFError("no supported NFC Forum Type 5 Capability Container")

        if cc[0] == self.CC_MAGIC:
            cc_size = 4
            mlen = cc[2]
            if mlen == 0:
                raise NDEFError("invalid short Type 5 Capability Container")
        elif cc[0] == self.CC_MAGIC_EXTENDED:
            cc_size = 8
            cc = self.read(0, cc_size)
            if cc[2] != 0:
                raise NDEFError("invalid extended Type 5 Capability Container")
            mlen = (cc[6] << 8) | cc[7]
            if mlen == 0:
                raise NDEFError("extended Type 5 Capability Container has zero size")
        else:
            raise NDEFError("no supported NFC Forum Type 5 Capability Container")

        # MLEN describes the T5T_Area after the CC, in units of eight bytes.
        end_address = cc_size + mlen * 8
        if end_address > self.capacity:
            # Some legacy smartphone-compatible CCs encode the whole physical
            # memory rather than memory-minus-CC. Accept only the final partial
            # eight-byte unit and clamp it to the actual detected capacity.
            if end_address - self.capacity <= 8:
                end_address = self.capacity
            else:
                raise NDEFError("Capability Container declares more memory than exists")
        if end_address <= cc_size:
            raise NDEFError("Capability Container declares more memory than exists")
        return cc, cc_size, end_address

    def format_ndef(self, usable_bytes=None):
        """Create a read/write NFC Forum Type 5 CC and an empty NDEF TLV.

        This overwrites the beginning of EEPROM. Devices of 16 Kbits or more
        receive the standards-compliant eight-byte CC (E2 magic), smaller
        devices receive the four-byte CC (E1 magic). ``usable_bytes`` is the
        T5T_Area size after the CC and is rounded down to an eight-byte unit.
        """
        if self.capacity >= 2048:
            cc_size = 8
        else:
            cc_size = 4

        maximum = ((self.capacity - cc_size) // 8) * 8
        if usable_bytes is None:
            usable_bytes = maximum
        usable_bytes = (int(usable_bytes) // 8) * 8
        if usable_bytes < 8 or usable_bytes > maximum:
            raise ValueError("usable_bytes must be 8..%d" % maximum)

        mlen = usable_bytes // 8
        if cc_size == 8:
            # E2: two-byte block addressing; b0 enables multiple-block reads.
            cc = bytes(
                (
                    self.CC_MAGIC_EXTENDED,
                    0x40,
                    0x00,
                    0x01,
                    0x00,
                    0x00,
                    mlen >> 8,
                    mlen & 0xFF,
                )
            )
        else:
            # E1: one-byte block addressing; b0 enables multiple-block reads.
            cc = bytes((self.CC_MAGIC, 0x40, mlen, 0x01))
        self.write(0, cc + bytes((self.NDEF_TLV, 0, self.TERMINATOR_TLV)))
        return usable_bytes

    def _find_ndef_tlv(self):
        _cc, offset, limit = self._read_cc()
        while offset < limit:
            tlv_type = self.read(offset, 1)[0]
            if tlv_type == 0x00:  # NULL TLV
                offset += 1
                continue
            if tlv_type == self.TERMINATOR_TLV:
                raise NDEFError("no NDEF TLV in the Type 5 data area")
            if offset + 2 > limit:
                break
            length_byte = self.read(offset + 1, 1)[0]
            if length_byte == 0xFF:
                if offset + 4 > limit:
                    break
                length_raw = self.read(offset + 2, 2)
                length = (length_raw[0] << 8) | length_raw[1]
                header_size = 4
            else:
                length = length_byte
                header_size = 2
            if offset + header_size + length > limit:
                raise NDEFError("TLV length exceeds the declared Type 5 memory")
            if tlv_type == self.NDEF_TLV:
                return offset, header_size, length, limit
            offset += header_size + length
        raise NDEFError("no NDEF TLV in the Type 5 data area")

    def read_ndef(self):
        """Return the raw NDEF message bytes (without Type 5 TLV framing)."""
        offset, header_size, length, _limit = self._find_ndef_tlv()
        return self.read(offset + header_size, length)

    def clear_ndef(self):
        """Leave the tag formatted but make its NDEF message empty."""
        try:
            _cc, offset, _limit = self._read_cc()
        except NDEFError:
            self.format_ndef()
            _cc, offset, _limit = self._read_cc()
        self.write(offset, bytes((self.NDEF_TLV, 0, self.TERMINATOR_TLV)))

    def write_ndef(self, message, verify=True):
        """Atomically-ish replace the raw NDEF message in the Type 5 TLV.

        The TLV length is kept zero while the payload is programmed, then
        committed last. A reader tapping during the write therefore sees an
        empty message rather than the new message with a stale/partial payload.
        """
        message = bytes(message)
        try:
            _cc, offset, limit = self._read_cc()
        except NDEFError:
            self.format_ndef()
            _cc, offset, limit = self._read_cc()

        if len(message) < 0xFF:
            empty_header = bytes((self.NDEF_TLV, 0))
            final_length = bytes((len(message),))
            length_offset = offset + 1
        elif len(message) <= 0xFFFF:
            empty_header = bytes((self.NDEF_TLV, 0xFF, 0, 0))
            final_length = bytes((len(message) >> 8, len(message) & 0xFF))
            length_offset = offset + 2
        else:
            raise ValueError("NDEF message exceeds the Type 5 TLV limit")

        payload_offset = offset + len(empty_header)
        if payload_offset + len(message) + 1 > limit:
            raise ValueError("NDEF message does not fit in the declared tag memory")

        self.write(offset, empty_header)
        if message:
            self.write(payload_offset, message)
        self.write(payload_offset + len(message), bytes((self.TERMINATOR_TLV,)))
        self.write(length_offset, final_length)

        if verify and self.read_ndef() != message:
            raise ST25DVError("NDEF read-back verification failed")

    @staticmethod
    def _make_well_known_record(record_type, payload):
        record_type = bytes(record_type)
        payload = bytes(payload)
        if len(record_type) > 0xFF:
            raise ValueError("NDEF record type is too long")
        if len(payload) <= 0xFF:
            # MB=1, ME=1, SR=1, TNF=1 (NFC Forum well-known type).
            return bytes((0xD1, len(record_type), len(payload))) + record_type + payload
        return (
            bytes((0xC1, len(record_type)))
            + len(payload).to_bytes(4, "big")
            + record_type
            + payload
        )

    @classmethod
    def _encode_uri_payload(cls, uri):
        uri_bytes = uri.encode("utf-8")
        best_code = 0
        best_length = 0
        for code in range(1, len(cls.URI_PREFIXES)):
            prefix = cls.URI_PREFIXES[code].encode("ascii")
            if len(prefix) > best_length and uri_bytes.startswith(prefix):
                best_code = code
                best_length = len(prefix)
        return bytes((best_code,)) + uri_bytes[best_length:]

    def write_ndef_uri(self, uri, verify=True):
        """Write one NFC Forum well-known URI record for phones/readers."""
        record = self._make_well_known_record(b"U", self._encode_uri_payload(uri))
        self.write_ndef(record, verify=verify)

    def write_ndef_text(self, text, language="en", verify=True):
        """Write one UTF-8 NFC Forum well-known Text record."""
        language_bytes = language.encode("ascii")
        if not 1 <= len(language_bytes) <= 63:
            raise ValueError("language must be a 1..63 byte ASCII language code")
        payload = bytes((len(language_bytes),)) + language_bytes + text.encode("utf-8")
        self.write_ndef(
            self._make_well_known_record(b"T", payload), verify=verify
        )

    @staticmethod
    def _parse_first_record(message):
        if len(message) < 3:
            raise NDEFError("NDEF message is too short")
        flags = message[0]
        type_length = message[1]
        position = 2
        if flags & 0x10:  # SR
            payload_length = message[position]
            position += 1
        else:
            if len(message) < position + 4:
                raise NDEFError("truncated NDEF payload length")
            payload_length = int.from_bytes(message[position : position + 4], "big")
            position += 4
        id_length = 0
        if flags & 0x08:  # IL
            if position >= len(message):
                raise NDEFError("truncated NDEF ID length")
            id_length = message[position]
            position += 1
        end = position + type_length + id_length + payload_length
        if end > len(message):
            raise NDEFError("truncated NDEF record")
        record_type = message[position : position + type_length]
        position += type_length + id_length
        return flags, record_type, message[position : position + payload_length]

    def read_ndef_uri(self):
        """Decode and return the first record when it is a well-known URI."""
        flags, record_type, payload = self._parse_first_record(self.read_ndef())
        if (flags & 0x07) != 1 or record_type != b"U" or not payload:
            raise NDEFError("first NDEF record is not a well-known URI")
        code = payload[0]
        if code >= len(self.URI_PREFIXES):
            raise NDEFError("unknown NDEF URI prefix code")
        return self.URI_PREFIXES[code] + payload[1:].decode("utf-8")

    def read_ndef_text(self):
        """Decode first UTF-8 well-known Text record as (language, text)."""
        flags, record_type, payload = self._parse_first_record(self.read_ndef())
        if (flags & 0x07) != 1 or record_type != b"T" or not payload:
            raise NDEFError("first NDEF record is not a well-known Text record")
        status = payload[0]
        if status & 0x80:
            raise NDEFError("UTF-16 NDEF Text records are not supported")
        language_length = status & 0x3F
        if 1 + language_length > len(payload):
            raise NDEFError("truncated NDEF Text record")
        language = payload[1 : 1 + language_length].decode("ascii")
        text = payload[1 + language_length :].decode("utf-8")
        return language, text

    def dynamic_status(self):
        """Return RF field, supply, security, interrupt, and mailbox status."""
        eh = self._read_at(self.user_address, self.REG_EH_CTRL_DYN, 1)[0]
        security = self._read_at(
            self.user_address, self.REG_I2C_SSO_DYN, 1
        )[0]
        interrupt = self._read_at(self.user_address, self.REG_IT_STS_DYN, 1)[0]
        mailbox = self._read_at(self.user_address, self.REG_MB_CTRL_DYN, 1)[0]
        return {
            "energy_harvesting_enabled": bool(eh & 0x01),
            "energy_harvesting_active": bool(eh & 0x02),
            "rf_field_present": bool(eh & 0x04),
            "vcc_present": bool(eh & 0x08),
            "i2c_security_session_open": bool(security & 0x01),
            "interrupt_status": interrupt,
            "mailbox_enabled": bool(mailbox & self.MB_ENABLED),
            "host_message_waiting": bool(mailbox & self.MB_HOST_PUT_MSG),
            "rf_message_waiting": bool(mailbox & self.MB_RF_PUT_MSG),
            "mailbox_control": mailbox,
        }

    def rf_field_present(self):
        """Return True while the chip currently detects a 13.56 MHz RF field."""
        value = self._read_at(self.user_address, self.REG_EH_CTRL_DYN, 1)[0]
        return bool(value & 0x04)

    def read_mailbox(self):
        """Read a waiting RF-to-I2C fast-transfer message.

        Fast transfer mode must already have been enabled in protected static
        configuration. Reading the last byte releases the mailbox to RF.
        """
        control = self._read_at(self.user_address, self.REG_MB_CTRL_DYN, 1)[0]
        if not control & self.MB_ENABLED:
            raise ST25DVError("fast-transfer mailbox is not enabled")
        if not control & self.MB_RF_PUT_MSG:
            return None
        length_minus_one = self._read_at(
            self.user_address, self.REG_MB_LEN_DYN, 1
        )[0]
        return self._read_at(
            self.user_address, self.REG_MAILBOX, length_minus_one + 1
        )

    def write_mailbox(self, data):
        """Put one I2C-to-RF message in an already-enabled, free mailbox."""
        data = bytes(data)
        if not 1 <= len(data) <= 256:
            raise ValueError("mailbox data must contain 1..256 bytes")
        control = self._read_at(self.user_address, self.REG_MB_CTRL_DYN, 1)[0]
        if not control & self.MB_ENABLED:
            raise ST25DVError("fast-transfer mailbox is not enabled")
        if control & (self.MB_HOST_PUT_MSG | self.MB_RF_PUT_MSG):
            raise ST25DVError("fast-transfer mailbox is not free")
        # The mailbox is volatile RAM: one transaction, no EEPROM ACK polling.
        packet = self._address_bytes(self.REG_MAILBOX) + data
        self.i2c.writeto(self.user_address, packet)


EEPROMNFC4701 = ST25DV16K
