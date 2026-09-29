"""Standalone MicroPython driver for Waveshare 1.28inch Touch LCD / Pico 2 W.

Uses only firmware modules: machine, framebuf, time. Defaults match the wiring
in waveshare-1.28-touch.md. Drawing changes RAM; call lcd.show() to refresh.
The RGB565 framebuffer uses 115,200 bytes. Use rgb() or the color constants
below: their bytes are arranged for direct SPI transmission on the Pico.

Sources:
https://files.waveshare.com/upload/3/3e/1.28inch_Touch_LCD_Pico.zip
  LCD initialization adapted from python/1.28inch_Touch_LCD.py.
  The archive's c/lib/LCD/LCD_1in28.c carries the MIT permission below.
https://github.com/peterhinch/micropython-touch/blob/master/touch/cst816s.py
  Interrupt flag, deferred I2C access and touch event decoding.
https://github.com/peterhinch/micropython-nano-gui/blob/master/drivers/gc9a01/gc9a01.py
  RGB565 byte order and sleep-out delay.

MIT License
Copyright (c) 2024 Peter Hinch (referenced/adapted driver portions)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from machine import Pin, SPI, I2C, PWM
import framebuf
from time import sleep_ms


def rgb(red, green, blue):
    """Convert 0-255 RGB channels to the driver's framebuffer color format."""
    value = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
    # framebuf stores a 16-bit pixel little-endian on RP2350; LCD wants MSB first.
    return ((value & 0xFF) << 8) | (value >> 8)


BLACK = rgb(0, 0, 0)
WHITE = rgb(255, 255, 255)
RED = rgb(255, 0, 0)
GREEN = rgb(0, 255, 0)
BLUE = rgb(0, 0, 255)
YELLOW = rgb(255, 255, 0)
CYAN = rgb(0, 255, 255)
MAGENTA = rgb(255, 0, 255)

# Manufacturer's analog/power/gamma initialization; keep undocumented registers.
_LCD_INIT = (
    (0xEF, b""), (0xEB, b"\x14"), (0xFE, b""), (0xEF, b""),
    (0xEB, b"\x14"), (0x84, b"\x40"), (0x85, b"\xff"),
    (0x86, b"\xff"), (0x87, b"\xff"), (0x88, b"\x0a"),
    (0x89, b"\x21"), (0x8A, b"\x00"), (0x8B, b"\x80"),
    (0x8C, b"\x01"), (0x8D, b"\x01"), (0x8E, b"\xff"),
    (0x8F, b"\xff"), (0xB6, b"\x00\x20"), (0x3A, b"\x05"),
    (0x90, b"\x08\x08\x08\x08"), (0xBD, b"\x06"),
    (0xBC, b"\x00"), (0xFF, b"\x60\x01\x04"),
    (0xC3, b"\x13"), (0xC4, b"\x13"), (0xC9, b"\x22"),
    (0xBE, b"\x11"), (0xE1, b"\x10\x0e"),
    (0xDF, b"\x21\x0c\x02"),
    (0xF0, b"\x45\x09\x08\x08\x26\x2a"),
    (0xF1, b"\x43\x70\x72\x36\x37\x6f"),
    (0xF2, b"\x45\x09\x08\x08\x26\x2a"),
    (0xF3, b"\x43\x70\x72\x36\x37\x6f"),
    (0xED, b"\x1b\x0b"), (0xAE, b"\x77"), (0xCD, b"\x63"),
    (0x70, b"\x07\x07\x04\x0e\x0f\x09\x07\x08\x03"),
    (0xE8, b"\x34"),
    (0x62, b"\x18\x0d\x71\xed\x70\x70\x18\x0f\x71\xef\x70\x70"),
    (0x63, b"\x18\x11\x71\xf1\x70\x70\x18\x13\x71\xf3\x70\x70"),
    (0x64, b"\x28\x29\xf1\x01\xf1\x00\x07"),
    (0x66, b"\x3c\x00\xcd\x67\x45\x45\x10\x00\x00\x00"),
    (0x67, b"\x00\x3c\x00\x00\x00\x01\x54\x10\x32\x98"),
    (0x74, b"\x10\x85\x80\x00\x00\x4e\x00"),
    (0x98, b"\x3e\x07"), (0x35, b""), (0x21, b""),
)


class GC9A01(framebuf.FrameBuffer):
    """240x240 framebuffer, built-in font/shapes and PWM backlight.

    spi may be an existing machine.SPI instance. Otherwise SPI1 is created.
    The default 20 MHz is deliberately modest for jumper wires.
    bgr=True matches Waveshare's panel.
    """

    def __init__(self, spi=None, spi_id=1, sck=10, mosi=11, miso=12,
                 cs=9, dc=14, rst=8, bl=15, baudrate=20_000_000,
                 brightness=0.7, bgr=True):
        self.width = self.height = 240
        self.cs = Pin(cs, Pin.OUT, value=1)
        self.dc = Pin(dc, Pin.OUT, value=0)
        self.rst = Pin(rst, Pin.OUT, value=1)
        self.pwm = PWM(Pin(bl))
        self.pwm.freq(5000)
        self.pwm.duty_u16(0)
        self.spi = spi if spi is not None else SPI(
            spi_id, baudrate=baudrate, polarity=0, phase=0, bits=8,
            sck=Pin(sck), mosi=Pin(mosi), miso=Pin(miso))
        self.buffer = bytearray(self.width * self.height * 2)
        self._view = memoryview(self.buffer)
        self._command = bytearray(1)
        self._window = bytearray(4)
        self._glyph = framebuf.FrameBuffer(bytearray(8), 8, 8, framebuf.MONO_HLSB)
        super().__init__(self.buffer, self.width, self.height, framebuf.RGB565)
        self.rst(0)
        sleep_ms(10)
        self.rst(1)
        sleep_ms(120)
        for command, data in _LCD_INIT:
            self._write(command, data)
        # Match the manufacturer's MicroPython orientation (MY + ML + BGR).
        self._write(0x36, b"\x98" if bgr else b"\x90")
        self._write(0x11)
        sleep_ms(120)
        self._write(0x29)
        sleep_ms(20)
        self.fill(BLACK)
        self.show()
        self.backlight(brightness)

    def _write(self, command, data=None):
        self._command[0] = command
        self.dc(0)
        self.cs(0)
        try:
            self.spi.write(self._command)
            if data:
                self.dc(1)
                self.spi.write(data)
        finally:
            self.cs(1)

    def _set_window(self, x0, y0, x1, y1):
        self._window[0] = self._window[2] = 0
        self._window[1], self._window[3] = x0, x1
        self._write(0x2A, self._window)
        self._window[1], self._window[3] = y0, y1
        self._write(0x2B, self._window)
        self._write(0x2C)

    def show(self):
        """Send all framebuffer pixels to the LCD."""
        self._set_window(0, 0, 239, 239)
        self.dc(1)
        self.cs(0)
        try:
            self.spi.write(self._view)
        finally:
            self.cs(1)

    def show_region(self, x, y, width, height):
        """Refresh a clipped rectangle from the framebuffer."""
        x1 = min(240, x + width)
        y1 = min(240, y + height)
        x, y = max(0, x), max(0, y)
        if x >= x1 or y >= y1:
            return
        self._set_window(x, y, x1 - 1, y1 - 1)
        self.dc(1)
        self.cs(0)
        try:
            for row in range(y, y1):
                start = (row * 240 + x) * 2
                self.spi.write(self._view[start:start + (x1 - x) * 2])
        finally:
            self.cs(1)

    def backlight(self, brightness):
        """Brightness from 0.0 (off) to 1.0 (full)."""
        if not 0 <= brightness <= 1:
            raise ValueError("brightness must be between 0 and 1")
        self.pwm.duty_u16(int(brightness * 65535))

    def text_scaled(self, text, x, y, color=WHITE, scale=1):
        """Draw the built-in 8x8 font, enlarged by an integer scale."""
        if not isinstance(scale, int) or scale < 1:
            raise ValueError("scale must be a positive integer")
        if scale == 1:
            self.text(text, x, y, color)
            return
        for char in text:
            self._glyph.fill(0)
            self._glyph.text(char, 0, 0, 1)
            for gy in range(8):
                for gx in range(8):
                    if self._glyph.pixel(gx, gy):
                        self.fill_rect(x + gx * scale, y + gy * scale,
                                       scale, scale, color)
            x += 8 * scale

    def deinit(self):
        """Turn off backlight and release PWM. Leave a supplied SPI bus intact."""
        self.pwm.duty_u16(0)
        self.pwm.deinit()


GESTURES = {0: "none", 1: "up", 2: "down", 3: "left", 4: "right",
            5: "tap", 11: "double tap", 12: "long press"}


class CST816S:
    """CST816S/T touch controller, with all I2C access in read(), never in IRQ.

    read() returns None if no new interrupt and polling is unavailable, or on
    I2C failure. Otherwise it returns x/y, raw_x/raw_y, fingers, event (0=down,
    1=up, 2=contact), pressed, gesture and fresh (an IRQ prompted this read).
    A sample can describe a release; polling can return the same sample again.
    Read samples often (5-10 ms); this is latest-state delivery, not an event
    queue. configured/version may remain false/None until the first touch.
    Orientation flags map raw coordinates; the demo determines these by taps.
    """

    def __init__(self, i2c=None, i2c_id=1, sda=6, scl=7, irq=17, rst=16,
                 address=0x15, frequency=400_000, swap_xy=False,
                 invert_x=False, invert_y=False):
        self.i2c = i2c if i2c is not None else I2C(
            i2c_id, sda=Pin(sda), scl=Pin(scl), freq=frequency)
        self.address = address
        self.swap_xy, self.invert_x, self.invert_y = swap_xy, invert_x, invert_y
        self.rst = Pin(rst, Pin.OUT, value=1)
        self.irq = Pin(irq, Pin.IN, Pin.PULL_UP)
        self._data = bytearray(6)
        self._pending = False
        self.irq_count = 0
        self.errors = 0
        self.last_error = None
        self.version = None
        self.configured = False
        # The rising edge is the end of the active-low interrupt pulse.
        self.irq.irq(handler=self._interrupt, trigger=Pin.IRQ_RISING, hard=True)
        self.rst(0)
        sleep_ms(5)
        self.rst(1)
        sleep_ms(50)
        self._configure()  # NACK before first touch is allowed on CST816S.

    def _interrupt(self, pin):
        self._pending = True
        self.irq_count = (self.irq_count + 1) & 0xFFFF

    def _configure(self):
        try:
            self.i2c.writeto_mem(self.address, 0xFE, b"\x01")  # Disable auto sleep.
            self.i2c.writeto_mem(self.address, 0xFA, b"\x70")  # Touch/change/motion.
            self.i2c.writeto_mem(self.address, 0xEC, b"\x01")  # Enable double tap.
        except OSError as error:
            self.last_error = error
            self.configured = False
            return
        self.configured = True
        self.last_error = None
        try:
            self.version = tuple(self.i2c.readfrom_mem(self.address, 0xA7, 3))
        except OSError:
            pass

    def read(self):
        if not self._pending and not self.configured and self.irq.value():
            return None
        fresh = self._pending
        self._pending = False
        try:
            self.i2c.readfrom_mem_into(self.address, 0x01, self._data)
        except OSError as error:
            self.errors += 1
            self.last_error = error
            self.configured = False
            return None
        data = self._data
        raw_x = ((data[2] & 0x0F) << 8) | data[3]
        raw_y = ((data[4] & 0x0F) << 8) | data[5]
        if raw_x >= 240 or raw_y >= 240:
            return None
        x, y = self.map_point(raw_x, raw_y)
        event = (data[2] >> 6) & 3
        fingers = data[1] & 0x0F
        pressed = fingers > 0 and event in (0, 2) and data[0] != 5
        sample = {"x": x, "y": y, "raw_x": raw_x, "raw_y": raw_y,
                  "event": event, "fingers": fingers,
                  "pressed": pressed, "gesture": data[0], "fresh": fresh}
        if not self.configured:
            self._configure()
        return sample

    def map_point(self, x, y):
        if self.swap_xy:
            x, y = y, x
        if self.invert_x:
            x = 239 - x
        if self.invert_y:
            y = 239 - y
        return x, y

    def deinit(self):
        self.irq.irq(handler=None)


class TouchLCD:
    """Convenience wrapper for the documented Pico 2 W wiring."""

    def __init__(self, baudrate=20_000_000, brightness=0.7, bgr=True):
        self.lcd = GC9A01(baudrate=baudrate, brightness=brightness, bgr=bgr)
        try:
            self.touch = CST816S()
        except Exception:
            self.lcd.deinit()
            raise

    def deinit(self):
        self.touch.deinit()
        self.lcd.deinit()
