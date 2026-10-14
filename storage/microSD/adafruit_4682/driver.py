# SPDX-License-Identifier: GPL-3.0-or-later
# 4-bit SD memory driver for ARM MicroPython on Raspberry Pi Pico 2 W.
# Distributed without warranty; see LICENSE.txt
"""Copy this single file to the Pico as driver.py. Requires rp2.DMA/asm_thumb.
A whole PIO block is reserved for two SMs; default PIO2 avoids the Wi-Fi PIO.
"""
import os
import time
import machine
import micropython
import rp2
import uctypes
from array import array
from micropython import const

_DMA_BASE = const(0x50000000)
_R1_ERRORS = const(0xFDFFE008)


@rp2.asm_pio(out_init=rp2.PIO.IN_HIGH, set_init=rp2.PIO.IN_HIGH,
             sideset_init=rp2.PIO.OUT_HIGH, autopull=True, autopush=True,
             out_shiftdir=rp2.PIO.SHIFT_LEFT, in_shiftdir=rp2.PIO.SHIFT_LEFT)
def _command():
    mov(osr, null).side(1)[1]
    label("idle")
    mov(y, invert(status)).side(0)[1]
    jmp(not_y, "idle").side(1)[1]
    out(null, 32).side(0)[1]
    out(x, 8).side(1)[1]
    set(pins, 1).side(0)[1]
    set(pindirs, 1).side(1)[1]
    label("send")
    out(pins, 1).side(0)[1]
    jmp(x_dec, "send").side(1)[1]
    set(pindirs, 0).side(0)[1]
    out(x, 8).side(1)[1]
    nop().side(0)[1]
    jmp(not_x, "done").side(1)[1]
    label("wait")
    nop().side(0)[1]
    jmp(pin, "wait").side(1)[1]
    label("receive")
    in_(pins, 1).side(0)[1]
    jmp(x_dec, "receive").side(1)[1]
    label("done")
    push().side(0)[1]


@rp2.asm_pio(out_init=(rp2.PIO.IN_HIGH,) * 4,
             set_init=(rp2.PIO.IN_HIGH,) * 4, autopush=True,
             in_shiftdir=rp2.PIO.SHIFT_LEFT, fifo_join=rp2.PIO.JOIN_RX)
def _receive():
    mov(x, y)
    wait(0, pin, 0)
    wait(1, pin, 30)[3]  # CLK = DAT0 - 2, modulo 32
    label("nibbles")
    in_(pins, 4)[2]
    jmp(x_dec, "nibbles")


@rp2.asm_pio(out_init=(rp2.PIO.IN_HIGH,) * 4,
             set_init=(rp2.PIO.IN_HIGH,) * 4, autopull=True,
             out_shiftdir=rp2.PIO.SHIFT_LEFT, in_shiftdir=rp2.PIO.SHIFT_LEFT)
def _transmit():
    # 14 instructions; together with the 18-instruction command SM fits in 32.
    # Each packet: count=1048, start, 128 data words, two CRC words, end.
    # PULL is an autopull barrier; discard the partial end word before wrapping.
    pull(block)
    out(x, 32)
    set(pindirs, 15)
    wait(0, pin, 30)
    wait(1, pin, 30)[4]
    label("nibbles")
    out(pins, 4)[1]
    jmp(x_dec, "nibbles")[1]
    set(pindirs, 0)[1]
    set(y, 31)[1]
    label("response")
    in_(pins, 1)[1]
    jmp(y_dec, "response")[1]
    wait(1, pin, 0)[1]
    push(block)[1]
    # With autopull, PULL does not replace a partially consumed OSR.
    # Discard the seven unused end nibbles explicitly; the next header may
    # autopull here, so the PULL at wrap must act as a barrier, not a second pop.
    out(null, 32)[1]


@micropython.asm_thumb
def _pack(r0, r1, r2):
    # src -> packets, n sectors. CRC lanes interleaved in two 32-bit registers.
    cmp(r2, 0)
    beq(end)
    label(sector)
    movw(r6, 1048)
    str(r6, [r1, 0])
    movwt(r6, 0xfffffff0)
    str(r6, [r1, 4])
    add(r1, 8)
    mov(r4, 0)  # high CRC
    mov(r5, 0)  # low CRC
    mov(r3, 128)
    label(word)
    ldr(r6, [r0, 0])
    data(2, 0xba36)  # REV r6,r6
    str(r6, [r1, 0])
    add(r0, 4)
    add(r1, 4)
    lsr(r7, r4, 16)
    eor(r7, r4)
    lsr(r4, r6, 16)
    eor(r7, r4)
    eor(r6, r7)  # v = high ^ high>>16 ^ data>>16 ^ data
    lsr(r4, r6, 12)
    eor(r4, r5)
    lsl(r7, r6, 16)
    eor(r4, r7)
    lsl(r5, r6, 20)
    eor(r5, r6)
    sub(r3, 1)
    bne(word)
    str(r4, [r1, 0])
    str(r5, [r1, 4])
    movwt(r6, 0xffffffff)
    str(r6, [r1, 8])
    add(r1, 12)
    sub(r2, 1)
    bne(sector)
    label(end)
    mov(r0, 0)


@micropython.asm_thumb
def _check(r0, r1, r2, r3):
    # src, received wire-order CRC bytes, blocks, words per block. 0 = valid.
    push({r3})
    cmp(r2, 0)
    beq(good)
    label(sector)
    data(2, 0x9b00)  # LDR r3,[sp,#0]
    mov(r4, 0)
    mov(r5, 0)
    label(word)
    ldr(r6, [r0, 0])
    data(2, 0xba36)  # REV r6,r6
    add(r0, 4)
    lsr(r7, r4, 16)
    eor(r7, r4)
    lsr(r4, r6, 16)
    eor(r7, r4)
    eor(r6, r7)
    lsr(r4, r6, 12)
    eor(r4, r5)
    lsl(r7, r6, 16)
    eor(r4, r7)
    lsl(r5, r6, 20)
    eor(r5, r6)
    sub(r3, 1)
    bne(word)
    ldr(r6, [r1, 0])
    data(2, 0xba36)
    cmp(r4, r6)
    bne(bad)
    ldr(r6, [r1, 4])
    data(2, 0xba36)
    cmp(r5, r6)
    bne(bad)
    add(r1, 8)
    sub(r2, 1)
    bne(sector)
    label(good)
    mov(r0, 0)
    b(end)
    label(bad)
    mov(r0, 1)
    label(end)
    pop({r3})


@micropython.viper
def _descriptors(dst, data_addr: uint, crc_addr: uint, n: int, words: int):
    p = ptr32(dst)
    for i in range(n):
        p[i * 4] = data_addr + uint(i * words * 4)
        p[i * 4 + 1] = uint(words)
        p[i * 4 + 2] = crc_addr + uint(i * 8)
        p[i * 4 + 3] = 2
    p[n * 4] = 0
    p[n * 4 + 1] = 0  # null trigger terminates the chain


@micropython.viper
def _crc7(buf) -> int:
    p = ptr8(buf)
    crc = 0
    for i in range(int(len(buf))):
        b = int(p[i])
        for bit in range(7, -1, -1):
            crc = (crc << 1) ^ (0x09 if ((crc >> 6) ^ (b >> bit)) & 1 else 0)
        crc &= 127
    return (crc << 1) | 1


def _capacity(csd):
    if csd[0] >> 6 != 1:
        raise OSError("only CSD v2 SDHC/SDXC cards supported")
    return (((csd[7] & 63) << 16 | csd[8] << 8 | csd[9]) + 1) * 1024


@micropython.viper
def _write_status(word: uint):
    # Start, three status bits, end. Leading idle ones precede the token.
    if word == uint(0xffffffff):
        raise OSError("missing SD write response")
    while word & uint(0x80000000):
        word = (word << 1) & uint(0xffffffff)
    status = int(word >> 28) & 7
    end_bit = int(word & uint(0x08000000))
    if status == 2:
        if end_bit != 0:
            return
    raise OSError("SD write rejected: invalid status/end token")


class SDCard:
    """Synchronous 512-byte block device for os.VfsFat.

    frequency is an upper bound (default 37.5 MHz at 150 MHz clk_sys).
    Integer SM dividers keep data sampling phase-locked to the command clock.
    max_blocks caps a burst and preallocates its scratch buffers (default 128).
    Requires exclusive ownership of the selected PIO block while alive.
    """
    def __init__(self, clk=10, cmd=11, data0=12, frequency=37_500_000,
                 sm_cmd=8, sm_data=9, max_blocks=128, timeout_ms=3000):
        if "RP2350" not in os.uname().machine:
            raise OSError("requires ARM RP2350 / Pico 2 MicroPython")
        if not hasattr(rp2, "DMA"):
            raise OSError("firmware needs rp2.DMA")
        if clk != data0 - 2 or not 2 <= data0 <= 19:
            raise ValueError("CLK must be DAT0-2; DAT0..3 consecutive GP2..22")
        if cmd in (clk, data0, data0 + 1, data0 + 2, data0 + 3) or not 0 <= cmd <= 22:
            raise ValueError("CMD must be a distinct exposed GPIO")
        if not 0 <= sm_cmd < 12 or not 0 <= sm_data < 12 or sm_cmd // 4 != sm_data // 4 or sm_cmd == sm_data:
            raise ValueError("choose two distinct SMs in one PIO block")
        if not 1 <= max_blocks <= 1024 or not 400_000 <= frequency <= 50_000_000 or timeout_ms < 100:
            raise ValueError("invalid buffer size, clock, or timeout")
        self._closed = False
        self._failed = False
        self._in_use = False
        self._cpu = machine.freq()
        self._limit = frequency
        self._max = max_blocks
        self._timeout = timeout_ms
        self._cmd_id, self._data_id = sm_cmd, sm_data
        self._pio_id = sm_cmd // 4
        self._pio = rp2.PIO(self._pio_id)
        self._base = 0x50200000 + self._pio_id * 0x100000
        self._dreq_tx = self._pio_id * 8 + sm_data % 4
        self._dreq_rx = self._dreq_tx + 4
        self._cmd = self._data = None
        self._program = None
        self._command_loaded = False
        self._dmas = []
        self._pins = (clk, cmd, data0, data0 + 1, data0 + 2, data0 + 3)
        self._clk_pin, self._cmd_pin, self._data_pin = clk, cmd, data0
        self._mask = sum(1 << p for p in self._pins)
        self._sync_saved = machine.mem32[self._base + 0x38] & self._mask
        self._pads_saved = [(0x40038004 + p * 4,
                             machine.mem32[0x40038004 + p * 4] & 1)
                            for p in self._pins]
        self.high_speed = False
        self.frequency = 0
        self.rca = 0
        try:
            self._tx_batch = min(max_blocks, 32)
            self._packets = (bytearray(self._tx_batch * 532),
                             bytearray(self._tx_batch * 532))
            self._crc = bytearray(max_blocks * 8)
            self._desc = array("I", [0] * (max_blocks * 4 + 2))
            self._responses = array("I", [0] * max_blocks)
            self._reply = array("I", [0] * 5)
            self._frame = bytearray(5)
            self._one = bytearray(512)
            for p in self._pins:
                machine.Pin(p, machine.Pin.IN, machine.Pin.PULL_UP)
            for reg, _ in self._pads_saved:
                machine.mem32[reg + 0x2000] = 1  # fast slew for high-speed SD bus
            for _ in range(3):
                self._dmas.append(rp2.DMA())
            self._dma, self._ctrl, self._cmd_dma = self._dmas
            self._set_clock(400_000)
            # Synchronous, host-generated clock: bypass the two-cycle input delay.
            machine.mem32[self._base + 0x2000 + 0x38] = self._mask
            self._init_card()
        except BaseException as exc:
            self.deinit()
            raise exc

    def _set_clock(self, limit):
        div = max(1, (self._cpu + 4 * limit - 1) // (4 * limit))
        self._sm_frequency = self._cpu // div
        self.frequency = self._cpu // (4 * div)
        if self._cmd is not None:
            self._cmd.active(0)
        self._cmd = rp2.StateMachine(self._cmd_id, _command, freq=self._sm_frequency,
                                     in_base=machine.Pin(self._cmd_pin),
                                     out_base=machine.Pin(self._cmd_pin),
                                     set_base=machine.Pin(self._cmd_pin),
                                     jmp_pin=machine.Pin(self._cmd_pin),
                                     sideset_base=machine.Pin(self._clk_pin))
        self._command_loaded = True
        # STATUS = TX FIFO level < 2. RP2350 has two STATUS_SEL bits.
        reg = self._base + 0xc8 + (self._cmd_id % 4) * 0x18 + 4
        machine.mem32[reg] = (machine.mem32[reg] & ~0x7f) | 2
        # Overwrite divider: freq's floor arithmetic could produce a fractional
        # divider; RX/TX assume exactly four SM ticks per SD clock cycle.
        machine.mem32[reg - 4] = div << 16
        self._cmd.active(1)
        self._divider = div

    def _data_mode(self, program, words=128):
        if self._data is not None:
            self._data.active(0)
        if self._program is not program:
            if self._program is not None:
                self._pio.remove_program(self._program)
                self._program = None
            # Track ownership only after StateMachine loads the program.
            self._data = rp2.StateMachine(self._data_id, program,
                                         freq=self._sm_frequency,
                                         in_base=machine.Pin(self._data_pin),
                                         out_base=machine.Pin(self._data_pin),
                                         set_base=machine.Pin(self._data_pin))
            self._program = program
        else:
            self._data.init(program, freq=self._sm_frequency,
                            in_base=machine.Pin(self._data_pin),
                            out_base=machine.Pin(self._data_pin),
                            set_base=machine.Pin(self._data_pin))
        machine.mem32[self._base + 0xc8 + (self._data_id % 4) * 0x18] = self._divider << 16
        if program is _receive:
            # JOIN_RX removes TX FIFO. Load Y with temporarily
            # unjoined FIFOs, then restore JOIN_RX before activation.
            shift = self._base + 0xd0 + (self._data_id % 4) * 0x18
            saved = machine.mem32[shift]
            machine.mem32[shift] = saved & ~(3 << 30)
            self._data.put(words * 8 + 15)
            # Integer opcodes avoid invoking the Python PIO assembler for each
            # transfer. These are pull(block) and out(y, 32), respectively.
            self._data.exec(0x80a0)
            self._data.exec(0x6040)
            machine.mem32[shift] = saved

    @micropython.native
    def _command_call(self, cmd, arg=0, bits=48, kind="r1"):
        for i in range(5):
            self._frame[i] = (0x40 | cmd) if i == 0 else (arg >> ((4 - i) * 8)) & 255
        crc = _crc7(self._frame)
        w0 = (47 << 24) | ((0x40 | cmd) << 16) | (arg >> 16)
        w1 = ((arg & 65535) << 16) | (crc << 8) | (bits - 1 if bits else 0)
        count = (bits + 31) // 32 if bits else 1
        dma = self._cmd_dma
        dma.config(read=self._base + 0x20 + (self._cmd_id % 4) * 4,
                   write=self._reply, count=count,
                   ctrl=dma.pack_ctrl(size=2, inc_read=False, inc_write=True,
                                      treq_sel=self._pio_id * 8 + self._cmd_id % 4 + 4),
                   trigger=True)
        # Two-word TX FIFO predicate prevents blocking put calls on normal use.
        self._cmd.put(w0)
        self._cmd.put(w1)
        try:
            self._wait_dma(dma, self._timeout, "CMD%d response" % cmd)
        except BaseException as exc:
            self._failed = True
            raise exc
        if not bits:
            return 0
        if bits == 136:
            packet = bytearray(17)
            for i in range(16):
                packet[i] = (self._reply[i // 4] >> (24 - (i % 4) * 8)) & 255
            packet[16] = self._reply[4] & 255
            payload = packet[1:]
            if packet[0] != 63 or payload[-1] != _crc7(payload[:-1]):
                raise OSError("CMD%d R2 framing/CRC" % cmd)
            return payload
        a, b = self._reply[0], self._reply[1]
        packet = bytes((a >> 24, (a >> 16) & 255, (a >> 8) & 255, a & 255, (b >> 8) & 255))
        if kind == "r3":
            if packet[0] != 63 or (b & 255) != 255:
                raise OSError("CMD%d R3 framing" % cmd)
        elif packet[0] != cmd or (b & 255) != _crc7(packet):
            raise OSError("CMD%d response framing/CRC" % cmd)
        result = ((a & 0xffffff) << 8) | ((b >> 8) & 255)
        if kind == "r1" and result & _R1_ERRORS:
            raise OSError("CMD%d card status 0x%08x" % (cmd, result))
        if kind == "r6" and result & 0xe000:
            raise OSError("CMD3 R6 status 0x%08x" % result)
        return result

    def _app(self, cmd, arg):
        status = self._command_call(55, self.rca)
        if not status & 32:
            raise OSError("card did not accept APP_CMD")
        return self._command_call(cmd, arg, kind="r3" if cmd == 41 else "r1")

    def _init_card(self):
        time.sleep_ms(10)  # continuous clock, >74 startup cycles
        self._command_call(0, bits=0)
        if self._command_call(8, 0x1aa, kind="r7") != 0x1aa:
            raise OSError("CMD8: requires SD v2 card at 3.3 V")
        start = time.ticks_ms()
        while True:
            self.ocr = self._app(41, 0x40ff8000)  # HCS, 2.7-3.6V, no 1.8V request
            if self.ocr & 0x80000000:
                break
            if time.ticks_diff(time.ticks_ms(), start) >= self._timeout:
                raise OSError("ACMD41 initialization timeout")
            time.sleep_ms(10)
        if not self.ocr & 0x40000000:
            raise OSError("requires block-addressed SDHC/SDXC card")
        self.cid = self._command_call(2, bits=136, kind="r2")
        self.rca = self._command_call(3, kind="r6") & 0xffff0000
        if not self.rca:
            raise OSError("CMD3 returned zero RCA")
        self.csd = self._command_call(9, self.rca, bits=136, kind="r2")
        self.sectors = _capacity(self.csd)
        self._command_call(7, self.rca)
        self._wait_ready()
        self._app(6, 2)  # four data lanes
        self._command_call(16, 512)
        self._set_clock(25_000_000)
        # CMD6 check/switch function group 1. Never exceed 25MHz before success.
        switch = bytearray(64)
        self._read_transfer(6, 0x00fffff1, switch, 1, 16)
        if switch[13] & 2 and not (switch[29] & 2):
            self._read_transfer(6, 0x80fffff1, switch, 1, 16)
            self.high_speed = (switch[16] & 15) == 1 and not (switch[29] & 2)
        self._set_clock(min(self._limit, 50_000_000 if self.high_speed else 25_000_000))

    @micropython.native
    def _wait_dma(self, dma, timeout, stage):
        start = time.ticks_ms()
        while dma.active():
            if time.ticks_diff(time.ticks_ms(), start) >= timeout:
                dma.active(0)
                raise OSError(stage + " timeout")
        if dma.ctrl & (7 << 29):
            raise OSError(stage + " DMA bus error")

    @micropython.native
    def _wait_ready(self):
        start = time.ticks_ms()
        pin = machine.Pin(self._data_pin)
        while not pin.value():
            if time.ticks_diff(time.ticks_ms(), start) >= self._timeout:
                raise OSError("DAT0 busy timeout")
        status = self._command_call(13, self.rca)
        while not status & 256 or ((status >> 9) & 15) != 4:
            if time.ticks_diff(time.ticks_ms(), start) >= self._timeout:
                raise OSError("card not ready in transfer state")
            status = self._command_call(13, self.rca)

    def _halt_data(self):
        # Abort controller first, then data, to prevent a late chained restart.
        self._ctrl.active(0)
        self._dma.active(0)
        if self._data is not None:
            self._data.active(0)
            self._data.exec(0xe080)  # set(pindirs, 0), release all data lines

    @micropython.native
    def _read_transfer(self, cmd, arg, buf, n, words=128):
        self._data_mode(_receive, words)
        _descriptors(self._desc, uctypes.addressof(buf), uctypes.addressof(self._crc), n, words)
        dma, ctrl = self._dma, self._ctrl
        dma.config(read=self._base + 0x20 + (self._data_id % 4) * 4,
                   write=0, count=0,
                   ctrl=dma.pack_ctrl(size=2, inc_read=False, inc_write=True,
                                      bswap=True, treq_sel=self._dreq_rx, chain_to=ctrl.channel))
        ctrl.config(read=self._desc, write=_DMA_BASE + dma.channel * 0x40 + 0x18,
                    count=2, ctrl=ctrl.pack_ctrl(size=2, inc_read=True, inc_write=True,
                                               ring_size=3, ring_sel=True))
        desc_addr = uctypes.addressof(self._desc)
        data_addr = uctypes.addressof(buf)
        crc_addr = uctypes.addressof(self._crc)
        terminal = desc_addr + (4 * n + 2) * 4
        verified = 0
        started = False
        try:
            ctrl.active(1)
            self._data.active(1)
            started = True
            self._command_call(cmd, arg)
            start = time.ticks_ms()
            while True:
                progress = ctrl.read
                # The next descriptor can be fetched only after both payload
                # and CRC have been written. Verify completed sectors while
                # the DMA/PIO continue receiving the remaining sectors.
                complete = min(n, max(0, (progress - desc_addr - 8) // 16))
                if complete - verified >= 8 or complete == n:
                    if _check(data_addr + verified * words * 4,
                              crc_addr + verified * 8, complete - verified, words):
                        raise OSError("SD read data CRC mismatch")
                    verified = complete
                if progress == terminal and not ctrl.active() and not dma.active():
                    break
                if (ctrl.ctrl | dma.ctrl) & (7 << 29):
                    raise OSError("SD RX DMA bus error")
                if time.ticks_diff(time.ticks_ms(), start) >= self._timeout:
                    raise OSError("SD read timeout")
            self._halt_data()
            if cmd == 18:
                self._command_call(12)
                self._wait_ready()
            if verified != n:
                raise OSError("SD read incomplete CRC verification")
        except BaseException as exc:
            self._halt_data()
            if started:
                self._failed = True
            raise exc

    @micropython.native
    def _write_transfer(self, block, buf, n):
        source = uctypes.addressof(buf)
        count = min(n, self._tx_batch)
        _pack(source, self._packets[0], count)
        self._data_mode(_transmit)
        self._ctrl.config(read=self._base + 0x20 + (self._data_id % 4) * 4,
                          write=self._responses, count=n,
                          ctrl=self._ctrl.pack_ctrl(size=2, inc_read=False, inc_write=True,
                                                    treq_sel=self._dreq_rx), trigger=True)
        txctrl = self._dma.pack_ctrl(size=2, inc_read=True, inc_write=False,
                                    treq_sel=self._dreq_tx)
        txfifo = self._base + 0x10 + (self._data_id % 4) * 4
        self._dma.config(read=self._packets[0], write=txfifo,
                         count=count * 133, ctrl=txctrl)
        try:
            self._command_call(25 if n > 1 else 24, block)
            self._dma.active(1)
            self._data.active(1)
            first, slot = count, 1
            while first < n:
                count = min(n - first, self._tx_batch)
                # CPU prepares the other buffer while DMA transmits this one.
                # Reuse is safe after payload DMA completion, even if the last
                # sector is still being programmed: FIFO holds its tail.
                _pack(source + first * 512, self._packets[slot], count)
                self._wait_dma(self._dma, self._timeout, "SD write payload")
                self._dma.config(read=self._packets[slot], write=txfifo,
                                 count=count * 133, ctrl=txctrl, trigger=True)
                first += count
                slot ^= 1
            self._wait_dma(self._ctrl, self._timeout, "SD write response/busy")
            self._wait_dma(self._dma, self._timeout, "SD write payload")
            self._halt_data()
            for i in range(n):
                _write_status(self._responses[i])
            if n > 1:
                self._command_call(12)
            self._wait_ready()  # includes card-reported programming completion
        except BaseException as exc:
            self._halt_data()
            self._failed = True  # do not retry a partially completed write
            raise exc

    def _enter(self, block, buf, offset, writable=False):
        if self._closed or self._failed:
            raise OSError("SDCard closed/failed; deinit and power-cycle card")
        if self._in_use:
            raise OSError("SDCard transfer already active")
        if machine.freq() != self._cpu:
            raise OSError("CPU clock changed; recreate SDCard")
        if offset is not None:
            raise ValueError("only the simple 512-byte block protocol is supported")
        view = memoryview(buf)
        if writable:
            if not isinstance(buf, (bytearray, memoryview)):
                raise ValueError("read buffer must be a writable byte buffer")
            if len(view):
                try:
                    view[0] = view[0]  # reject readonly memoryviews before DMA
                except (TypeError, ValueError):
                    raise ValueError("read buffer must be writable")
        size = len(view) * (view.itemsize if hasattr(view, "itemsize") else 1)
        if size == 0 or size % 512 or not isinstance(block, int) or block < 0 or block + size // 512 > self.sectors:
            raise ValueError("buffer must contain whole sectors within the card")
        if uctypes.addressof(buf) & 3:
            raise ValueError("DMA buffer must be 4-byte aligned")
        self._in_use = True
        return size // 512

    def readblocks(self, block_num, buf, offset=None):
        n = self._enter(block_num, buf, offset, True)
        try:
            # Allocate all memoryviews before starting DMA. Byte buffers required.
            if not isinstance(buf, (bytearray, memoryview)):
                raise ValueError("readblocks requires bytearray or byte memoryview")
            view = memoryview(buf)
            if len(view) != n * 512:
                raise ValueError("use a byte-oriented buffer")
            for first in range(0, n, self._max):
                count = min(n - first, self._max)
                part = view[first * 512:(first + count) * 512]
                self._read_transfer(17 if count == 1 else 18, block_num + first, part, count)
            return 0
        finally:
            self._in_use = False

    def writeblocks(self, block_num, buf, offset=None):
        n = self._enter(block_num, buf, offset)
        try:
            view = memoryview(buf)
            if len(view) != n * 512:
                raise ValueError("use a byte-oriented buffer")
            for first in range(0, n, self._max):
                count = min(n - first, self._max)
                part = view[first * 512:(first + count) * 512]
                self._write_transfer(block_num + first, part, count)
            return 0
        finally:
            self._in_use = False

    def ioctl(self, op, arg):
        if op == 2:
            self.deinit()
            return 0
        if self._closed or self._failed:
            raise OSError("SDCard closed/failed")
        if op == 1:
            return 0
        if op == 3:
            if self._in_use:
                raise OSError("SDCard transfer already active")
            self._wait_ready()
            return 0
        if op == 4:
            return self.sectors
        if op == 5:
            return 512
        return None

    def deinit(self):
        if self._closed:
            return
        self._closed = True
        for dma in reversed(self._dmas):
            try:
                dma.active(0)
                dma.close()
            except Exception:
                pass
        if self._data is not None:
            self._data.active(0)
            self._data.exec(0xe080)  # set(pindirs, 0)
        if self._cmd is not None:
            self._cmd.active(0)
        if self._program is not None:
            self._pio.remove_program(self._program)
        if self._command_loaded:
            self._pio.remove_program(_command)
        machine.mem32[self._base + 0x3000 + 0x38] = self._mask
        machine.mem32[self._base + 0x2000 + 0x38] = self._sync_saved
        for p in self._pins:
            machine.Pin(p, machine.Pin.IN, machine.Pin.PULL_UP)
        for reg, slew in self._pads_saved:
            machine.mem32[reg + 0x3000] = 1
            machine.mem32[reg + 0x2000] = slew
