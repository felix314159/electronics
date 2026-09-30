"""Bluetooth LE mouse for the Pico 2 W / Waveshare round touchscreen.

Requires only touch_lcd.py drivers.
BT Pairing keys are saved on the Pico. Subsequent boots reconnect without calibration.
MicroPython must have Bluetooth pairing/bonding enabled for bonded HID hosts.
API: https://docs.micropython.org/en/latest/library/bluetooth.html
"""

import bluetooth
import struct
import json
import os
import binascii
from time import ticks_ms, ticks_diff, sleep_ms
from touch_lcd import TouchLCD, BLACK, WHITE, CYAN, GREEN, YELLOW, rgb

NAME = "Pico Touchpad"
SENSITIVITY = 1.8            # Mouse units per display pixel.
MOVE_THRESHOLD = 6           # Maximum displacement for a tap/hold.
MIN_TAP_MS = 8               # A brief contact still counts as a deliberate tap.
HOLD_MS = 700
DOUBLE_TAP_MS = 450          # First lift to second touch for left-button drag.
DOUBLE_TAP_RADIUS = 45       # Allow natural finger repositioning between taps.
RELEASE_MS = 12              # Reject release chatter without swallowing fast taps.
HOLD_RELEASE_MS = 80         # Confirm finger lift before releasing a held button.
LOST_TOUCH_MS = 250          # Cancel a gesture on missing I2C data.
TAP_ANIMATION_MS = 280
TRAIL_MS = 350
FADE_GREEN = tuple(rgb(0, level, 0) for level in (32, 64, 96, 128, 160, 192, 224, 255))
CALIBRATION_PRESS_MS = 100
CALIBRATION_RELEASE_MS = 250
ORIENTATION_FILE = "mouse_orientation.json"
BOND_FILE = "mouse_bonds.json"
STATUS = {}
TRACE_TOUCH = False

# Three buttons, signed relative X/Y and wheel; report ID 1 is carried by
# the Report Reference descriptor, not prepended to the BLE report value.
REPORT_MAP = bytes((
    0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x85, 0x01,
    0x09, 0x01, 0xA1, 0x00, 0x05, 0x09, 0x19, 0x01,
    0x29, 0x03, 0x15, 0x00, 0x25, 0x01, 0x95, 0x03,
    0x75, 0x01, 0x81, 0x02, 0x95, 0x01, 0x75, 0x05,
    0x81, 0x03, 0x05, 0x01, 0x09, 0x30, 0x09, 0x31,
    0x09, 0x38, 0x15, 0x81, 0x25, 0x7F, 0x75, 0x08,
    0x95, 0x03, 0x81, 0x06, 0xC0, 0xC0,
))


def save_json(path, value):
    with open(path + ".tmp", "w") as output:
        json.dump(value, output)
    os.rename(path + ".tmp", path)


class BLEMouse:
    def __init__(self):
        self.ble = bluetooth.BLE()
        self.conn = None
        self.encrypted = False
        self.suspended = False
        self.protocol = 1
        self.bonds = {}
        self.dirty = False
        self.advertise_pending = False
        self.release_at = None
        self.buttons = 0
        self.last_error = None
        self.session = 0
        try:
            with open(BOND_FILE) as source:
                for kind, key, value in json.load(source):
                    self.bonds[(kind, binascii.unhexlify(key))] = binascii.unhexlify(value)
        except OSError:
            pass
        # Local encryption/identity roots consumed by the pairing-enabled build (fixed identifiers, this is not secret or machine-unique information)
        for tag in (0x45524B59, 0x49524B59):
            key = (1, struct.pack("<I", tag))
            if key not in self.bonds:
                self.bonds[key] = os.urandom(16)
                self.dirty = True
        # Install the secret store before BTstack initializes and derives its
        # encryption/identity keys. Loading roots after active(True) is too late.
        self.ble.irq(self._irq)
        self.ble.active(True)
        self.ble.config(gap_name=NAME)
        try:
            self.ble.config(bond=True, mitm=False, io=3, le_secure=False)
            self.bonding = True
        except ValueError:
            self.bonding = False
            print("Firmware lacks Bluetooth bonding; host may reject HID pairing.")

        read = bluetooth.FLAG_READ
        write = bluetooth.FLAG_WRITE
        write_nr = bluetooth.FLAG_WRITE_NO_RESPONSE
        notify = bluetooth.FLAG_NOTIFY
        secure_read = read | (0x0200 if self.bonding else 0)
        secure_write = 0x1000 if self.bonding else 0
        uuid = bluetooth.UUID
        services = (
            (uuid(0x1812), (
                (uuid(0x2A4A), read),                    # HID Information
                (uuid(0x2A4B), secure_read),             # Report Map
                (uuid(0x2A4C), write_nr | secure_write), # HID Control Point
                (uuid(0x2A4E), read | write_nr | secure_write),
                (uuid(0x2A4D), secure_read | notify,
                 ((uuid(0x2908), read),)),               # Input + Report Reference
                (uuid(0x2A33), secure_read | notify),    # Boot mouse input
            )),
            (uuid(0x180A), (
                (uuid(0x2A29), read),                    # Manufacturer
                (uuid(0x2A24), read),                    # Model
            )),
        )
        handles, device = self.ble.gatts_register_services(services)
        (info, report_map, self.control, self.mode,
         self.report, reference, self.boot) = handles
        for handle, value in (
            (info, b"\x11\x01\x00\x02"),
            (report_map, REPORT_MAP), (self.mode, b"\x01"),
            (self.report, bytes(4)), (reference, b"\x01\x01"),
            (self.boot, bytes(3)), (device[0], b"Pico"),
            (device[1], b"Round Touchpad"),
        ):
            self.ble.gatts_write(handle, value)
        self.advertise()
        print("Bluetooth mouse:", NAME, "address", binascii.hexlify(self.ble.config('mac')[1]))

    def _irq(self, event, data):
        if event == 1:  # Central connected.
            self.conn = data[0]
            self.session += 1
            self.encrypted = False
            self.suspended = False
            self.protocol = 1
            self.buttons = 0
            self.release_at = None
            self.ble.gatts_write(self.mode, b"\x01")
        elif event == 2:
            self.conn = None
            self.session += 1
            self.encrypted = False
            self.buttons = 0
            self.release_at = None
            self.advertise_pending = True
        elif event == 3:
            handle = data[1]
            if handle == self.control:
                self.suspended = self.ble.gatts_read(handle) == b"\x00"
            elif handle == self.mode:
                self.protocol = 0 if self.ble.gatts_read(handle) == b"\x00" else 1
        elif event == 4:  # Successful GATT reads return zero (ATT success).
            return 0
        elif event == 28:
            self.encrypted = bool(data[1])
            print("Bluetooth encryption:", self.encrypted)
        elif event == 29:  # Retrieve bonding secret; copy memoryviews in IRQ.
            kind, index, key = data
            if key is not None:
                return self.bonds.get((kind, bytes(key)))
            values = [v for (k, _), v in self.bonds.items() if k == kind]
            return values[index] if index < len(values) else None
        elif event == 30:
            kind, key, value = data
            key = (kind, bytes(key))
            if value is None:
                self.bonds.pop(key, None)
            else:
                self.bonds[key] = bytes(value)
            self.dirty = True
            return True

    def advertise(self):
        # Flags, complete 16-bit HID service UUID, mouse appearance (0x03C2).
        payload = b"\x02\x01\x06\x03\x03\x12\x18\x03\x19\xc2\x03"
        name = NAME.encode()
        payload += bytes((len(name) + 1, 0x09)) + name
        self.ble.gap_advertise(100_000, adv_data=payload)
        self.advertise_pending = False

    def ready(self):
        return self.conn is not None and not self.suspended and (
            self.encrypted or not self.bonding)

    def send(self, buttons=0, dx=0, dy=0):
        if not self.ready():
            return False
        dx = max(-127, min(127, int(dx)))
        dy = max(-127, min(127, int(dy)))
        handle = self.boot if self.protocol == 0 else self.report
        report = struct.pack("Bbb", buttons, dx, dy)
        if self.protocol != 0:
            report += b"\x00"
        try:
            self.ble.gatts_write(handle, report)
            self.ble.gatts_notify(self.conn, handle, report)
            self.last_error = None
            return True
        except OSError as error:
            self.last_error = error
            return False

    def click(self, button, now):
        if self.release_at is None and self.send(button):
            self.buttons = button
            self.release_at = now
            print("Left click" if button == 1 else "Right click")
            return True
        return False

    def hold(self, button):
        # Complete a pending click before holding the second button-down.
        if self.release_at is not None and not self.send():
            return False
        if not self.send(button):
            return False
        self.buttons = button
        self.release_at = None
        return True

    def release(self, now):
        self.release_at = now
        if self.send():
            self.buttons = 0
            self.release_at = None

    def poll(self, now):
        if self.advertise_pending:
            self.advertise()
        # Keep retrying button-up on a temporary send error.
        if self.release_at is not None and ticks_diff(now, self.release_at) >= 30:
            if self.send():
                self.buttons = 0
                self.release_at = None
        if self.dirty:
            self.dirty = False
            snapshot = tuple(self.bonds.items())
            entries = [(kind, binascii.hexlify(key).decode(), binascii.hexlify(value).decode())
                       for (kind, key), value in snapshot]
            try:
                save_json(BOND_FILE, entries)
            except OSError as error:
                self.dirty = True
                print("Could not save Bluetooth bond:", error)

    def close(self):
        if self.conn is not None:
            self.send()
            self.ble.gap_disconnect(self.conn)
        self.ble.gap_advertise(None)
        self.ble.active(False)


class Touchpad:
    """Classify contacts independently of the controller's gesture IDs."""

    def __init__(self, mouse):
        self.mouse = mouse
        self.reset()

    def reset(self):
        if getattr(self, "dragging", False):
            self.mouse.release(ticks_ms())
        self.dragging = False
        self.second_tap = False
        self.last_tap = None
        self.down = False
        self.blocked = False
        self.up_since = None
        self.hold_up_since = None
        self.last_valid = None
        self.point = None
        self.action = ""
        self.rx = self.ry = 0.0
        self.tap_events = []
        self.trail = []

    def tap_animation(self, now, point, double=False):
        self.tap_events.append((now, point, double))
        self.tap_events = self.tap_events[-4:]

    def update(self, sample, now):
        if sample is None:
            self.hold_up_since = None  # Missing data cannot confirm a lift.
            if self.down and ticks_diff(now, self.last_valid) >= LOST_TOUCH_MS:
                if self.dragging:
                    self.mouse.release(now)
                self.dragging = self.second_tap = False
                self.last_tap = None
                self.down = False
                self.blocked = True  # Missing samples cannot manufacture a click.
                self.point = None
            return
        self.last_valid = now
        # A stale gesture ID must not turn a held finger into a release.
        pressed = sample["fingers"] > 0 and sample["event"] in (0, 2)
        if not pressed:
            if sample["fingers"] > 0 and sample["event"] != 1:
                self.hold_up_since = None
                return  # An unknown event is not evidence of finger lift.
            if self.down and self.dragging:
                if self.hold_up_since is None:
                    self.hold_up_since = now
                if ticks_diff(now, self.hold_up_since) < HOLD_RELEASE_MS:
                    return
            if self.down:
                duration = ticks_diff(now, self.start)
                if self.second_tap and not self.dragging and duration >= MIN_TAP_MS:
                    # A quick second tap still needs a full click pulse even
                    # if no held sample arrived before its release packet.
                    if self.mouse.click(1, now):
                        self.tap_animation(now, self.previous, True)
                    self.action = "LEFT CLICK"
                    if self.moved and (self.rx or self.ry):
                        self.mouse.send(self.mouse.buttons, self.rx, self.ry)
                if self.dragging:
                    if self.rx or self.ry:
                        self.mouse.send(self.mouse.buttons, self.rx, self.ry)
                    self.mouse.release(now)
                    self.dragging = False
                    self.hold_up_since = None
                    self.action = "DRAG END"
                elif not self.second_tap and not self.moved and not self.clicked and duration >= MIN_TAP_MS:
                    button = 2 if duration >= HOLD_MS else 1
                    if self.mouse.click(button, now):
                        self.tap_animation(now, self.previous)
                        if button == 1:
                            self.last_tap = (now, self.origin)
                    self.action = "RIGHT CLICK" if button == 2 else "LEFT CLICK"
                self.down = False
                self.blocked = True
                self.up_since = now
                self.point = None
            if self.up_since is None:
                self.up_since = now
            if ticks_diff(now, self.up_since) >= RELEASE_MS:
                self.blocked = False
            return
        self.hold_up_since = None
        # The next poll may already be the second press. Qualify the gap here
        # too, instead of requiring another no-touch poll to unlock input.
        if self.blocked and self.up_since is not None and ticks_diff(now, self.up_since) >= RELEASE_MS:
            self.blocked = False
        self.up_since = None
        if self.blocked:
            return
        x, y = sample["x"], sample["y"]
        self.point = (x, y)
        if not self.down:
            self.down = True
            self.start = now
            self.origin = self.previous = (x, y)
            self.moved = self.clicked = False
            self.rx = self.ry = 0.0
            self.action = "TOUCH"
            self.second_tap = False
            if self.last_tap is not None:
                when, point = self.last_tap
                self.second_tap = (0 <= ticks_diff(now, when) <= DOUBLE_TAP_MS and
                                  (x - point[0]) ** 2 + (y - point[1]) ** 2 <= DOUBLE_TAP_RADIUS ** 2)
            self.last_tap = None
            return
        ox, oy = self.origin
        if (x - ox) ** 2 + (y - oy) ** 2 > MOVE_THRESHOLD ** 2:
            self.moved = True
        if self.second_tap and not self.dragging and ticks_diff(now, self.start) >= MIN_TAP_MS:
            self.dragging = self.mouse.hold(1)
            if self.dragging:
                self.clicked = True
                self.tap_animation(now, (x, y), True)
        if self.moved:
            px, py = self.previous
            if (x, y) != (px, py):
                self.trail.append((now, px, py, x, y))
                self.trail = self.trail[-64:]
            # Preserve subpixel movement; cap each report to the HID range.
            self.rx += (x - px) * SENSITIVITY
            self.ry += (y - py) * SENSITIVITY
            dx = max(-127, min(127, int(self.rx)))
            dy = max(-127, min(127, int(self.ry)))
            if (dx or dy) and (not self.second_tap or self.dragging) and self.mouse.send(self.mouse.buttons, dx, dy):
                self.rx -= dx
                self.ry -= dy
            self.action = "DRAGGING" if self.dragging else "MOVING"
        elif self.dragging:
            self.action = "RIGHT HELD" if self.mouse.buttons == 2 else "LEFT HELD"
        elif not self.second_tap and not self.clicked and ticks_diff(now, self.start) >= HOLD_MS:
            self.dragging = self.mouse.hold(2)
            if self.dragging:
                self.clicked = True
                self.tap_animation(now, (x, y))
                self.action = "RIGHT HELD"
        self.previous = (x, y)


def centered(lcd, text, y, color=WHITE):
    lcd.text(text, (240 - len(text) * 8) // 2, y, color)


def screen(lcd, mouse, pad, now=None):
    if now is None:
        now = ticks_ms()
    lcd.fill(BLACK)
    if mouse.ready():
        pad.trail = [segment for segment in pad.trail if ticks_diff(now, segment[0]) < TRAIL_MS]
        pad.tap_events = [tap for tap in pad.tap_events if ticks_diff(now, tap[0]) < TAP_ANIMATION_MS]
        for when, x0, y0, x1, y1 in pad.trail:
            age = max(0, ticks_diff(now, when))
            color = FADE_GREEN[7 - min(7, age * 8 // TRAIL_MS)]
            lcd.line(x0, y0, x1, y1, color)
        for when, (x, y), double in pad.tap_events:
            age = max(0, ticks_diff(now, when))
            radius = 3 + age * 15 // TAP_ANIMATION_MS
            color = FADE_GREEN[7 - min(7, age * 8 // TAP_ANIMATION_MS)]
            lcd.ellipse(x, y, radius, radius, color)
            if double:
                lcd.ellipse(x, y, radius + 5, radius + 5, color)
        lcd.ellipse(119, 119, 118, 118, GREEN)
        lcd.show()
        return
    centered(lcd, "PICO TOUCHPAD", 36, CYAN)
    if mouse.conn is None:
        centered(lcd, "PAIR BLUETOOTH", 68, YELLOW)
        centered(lcd, NAME, 92)
    elif mouse.suspended:
        centered(lcd, "HOST SUSPENDED", 76, YELLOW)
    elif not mouse.ready():
        centered(lcd, "CONNECTING...", 76, YELLOW)
    centered(lcd, "Tap: left click", 152)
    centered(lcd, "Hold: right click", 176)
    lcd.show()


def wait_calibration_tap(touch):
    """Return one point after a debounced press and confirmed finger lift."""
    point = None
    press_since = None
    release_since = None
    last_message = ticks_ms()
    while True:
        sample = touch.read()
        now = ticks_ms()
        if sample is not None:
            if sample["fingers"] > 0 and sample["event"] in (0, 2):
                release_since = None
                if press_since is None:
                    press_since = now
                if ticks_diff(now, press_since) >= CALIBRATION_PRESS_MS:
                    point = (sample["raw_x"], sample["raw_y"])
            elif sample["fingers"] == 0 or sample["event"] == 1:
                press_since = None
                if point is not None:
                    if release_since is None:
                        release_since = now
                    elif ticks_diff(now, release_since) >= CALIBRATION_RELEASE_MS:
                        return point
            else:
                press_since = release_since = None
        else:
            press_since = release_since = None
        if ticks_diff(ticks_ms(), last_message) >= 3000:
            print("Waiting for tap + release. IRQ=%d configured=%s I2C errors=%d" %
                  (touch.irq_count, touch.configured, touch.errors))
            if touch.last_error is not None:
                print("Last I2C error:", touch.last_error,
                      "Check TP_SDA=GP6, TP_SCL=GP7, TP_INT=GP17, TP_RST=GP16.")
            last_message = ticks_ms()
        sleep_ms(5)


def transform(x, y, swap, flip_x, flip_y):
    if swap:
        x, y = y, x
    return (239 - x if flip_x else x, 239 - y if flip_y else y)


def choose_orientation(raw_points, targets):
    """Find the best of the eight axis-swap/mirror mappings."""
    best = None
    for swap in (False, True):
        for flip_x in (False, True):
            for flip_y in (False, True):
                error = 0
                worst = 0
                for raw, target in zip(raw_points, targets):
                    x, y = transform(raw[0], raw[1], swap, flip_x, flip_y)
                    distance = (x - target[0]) ** 2 + (y - target[1]) ** 2
                    error += distance
                    worst = max(worst, distance)
                if best is None or error < best[0]:
                    best = (error, worst, swap, flip_x, flip_y)
    return best


def calibrate_touch(lcd, touch):
    targets = ((120, 52), (188, 120), (120, 188))
    print("Touch orientation: hold each target briefly, then lift finger.")
    while True:
        raw_points = []
        for number, (x, y) in enumerate(targets):
            lcd.fill(BLACK)
            centered(lcd, "TOUCH SETUP %d/3" % (number + 1), 92)
            centered(lcd, "Hold target + lift", 144, CYAN)
            lcd.ellipse(x, y, 14, 14, YELLOW)
            lcd.hline(x - 7, y, 15, WHITE)
            lcd.vline(x, y - 7, 15, WHITE)
            lcd.show()
            point = wait_calibration_tap(touch)
            print("Target", (x, y), "raw touch", point)
            raw_points.append(point)
        error, worst, swap, flip_x, flip_y = choose_orientation(raw_points, targets)
        if worst <= 30 * 30:
            touch.swap_xy, touch.invert_x, touch.invert_y = swap, flip_x, flip_y
            print("Touch mapping: swap_xy=%s invert_x=%s invert_y=%s" %
                  (swap, flip_x, flip_y))
            print("Touch version bytes:", touch.version)
            return
        print("Taps missed targets; retrying orientation setup.")
        lcd.fill(BLACK)
        centered(lcd, "PLEASE RETRY", 104, YELLOW)
        lcd.show()
        sleep_ms(1200)


def main(calibrate=False):
    global STATUS
    STATUS = {"stage": "display setup"}
    board = TouchLCD()
    mouse = None
    try:
        try:
            with open(ORIENTATION_FILE) as source:
                mapping = json.load(source)
            board.touch.swap_xy, board.touch.invert_x, board.touch.invert_y = mapping
        except (OSError, ValueError):
            calibrate = True
        if calibrate:
            STATUS = {"stage": "calibration"}
            calibrate_touch(board.lcd, board.touch)
            save_json(ORIENTATION_FILE, [board.touch.swap_xy,
                      board.touch.invert_x, board.touch.invert_y])
        # Advertise only once calibration is complete and poll() can persist
        # new bonds and restart advertising after a failed connection.
        mouse = BLEMouse()
        mouse.poll(ticks_ms())
        pad = Touchpad(mouse)
        last_draw = ticks_ms() - 200
        session = mouse.session
        was_ready = False
        trace_state = None
        last_trace = ticks_ms()
        print("Slide to move; tap to left-click; hold 700 ms to hold right button until lift.")
        while True:
            now = ticks_ms()
            mouse.poll(now)
            ready = mouse.ready()
            if mouse.session != session or ready != was_ready:
                pad.reset()
                pad.blocked = True  # Lift before using a newly connected mouse.
                session, was_ready = mouse.session, ready
                print("Mouse ready:", ready)
            sample = board.touch.read()
            if ready:
                pad.update(sample, now)
            if TRACE_TOUCH:
                state = (pad.down, pad.dragging, mouse.buttons, pad.hold_up_since is not None)
                if state != trace_state or (pad.down and ticks_diff(now, last_trace) >= 100):
                    raw = None if sample is None else (sample["fingers"], sample["event"],
                          sample["gesture"], sample.get("fresh"), sample["x"], sample["y"])
                    print("TOUCH", now, raw, state, "pending", mouse.release_at,
                          "errors", board.touch.errors, "action", pad.action)
                    trace_state, last_trace = state, now
            draw_interval = 40 if ready and (pad.tap_events or pad.trail) else 150
            if ticks_diff(now, last_draw) >= draw_interval:
                STATUS = {"stage": "touchpad", "time": now,
                          "connected": mouse.conn is not None,
                          "encrypted": mouse.encrypted,
                          "suspended": mouse.suspended,
                          "ready": ready, "touch_errors": board.touch.errors}
                screen(board.lcd, mouse, pad, now)
                last_draw = now
            sleep_ms(8)
    finally:
        if mouse is not None:
            mouse.close()
        board.deinit()


if __name__ == "__main__":
    main()
