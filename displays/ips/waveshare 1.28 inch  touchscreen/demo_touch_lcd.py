"""Pico 2 W / Waveshare 1.28inch Touch LCD hardware test.

Displays various colors, brightness modes and user-touch-interaction challenges.
At the end shows the user a free-form field where he can draw anything and the 
program determines the swipe direction.
"""

import math
from time import sleep_ms, ticks_ms, ticks_diff
from touch_lcd import (
    TouchLCD, rgb, BLACK, WHITE, RED, GREEN, BLUE, YELLOW, CYAN, MAGENTA,
    GESTURES,
)

SPI_BAUDRATE = 20_000_000
BGR = True
BRIGHTNESS = 0.7
TAP_PRESS_MS = 100        # Require slight hold to register touch to prevent being too sensitive in the touch-here challenges
TAP_RELEASE_MS = 250      # Require a finger lift before showing the next touch-this target
DRAW_X, DRAW_Y, DRAW_W, DRAW_H = 24, 60, 192, 100


def centered(lcd, text, y, color=WHITE, scale=1):
    lcd.text_scaled(text, (240 - len(text) * 8 * scale) // 2, y, color, scale)


def circle(lcd, x, y, radius, color, fill=False):
    for dy in range(-radius, radius + 1):
        dx = int(math.sqrt(radius * radius - dy * dy))
        if fill:
            lcd.hline(x - dx, y + dy, 2 * dx + 1, color)
        else:
            lcd.pixel(x - dx, y + dy, color)
            lcd.pixel(x + dx, y + dy, color)


def display_test(lcd):
    print("Display: check that each color matches its label.")
    for name, color in (("RED", RED), ("GREEN", GREEN), ("BLUE", BLUE),
                        ("WHITE", WHITE), ("BLACK", BLACK)):
        lcd.fill(color)
        centered(lcd, name, 104, BLACK if color == WHITE else WHITE, 3)
        lcd.show()
        sleep_ms(700)

    lcd.fill(BLACK)
    centered(lcd, "RGB GRADIENTS", 42)
    for x in range(40, 200):
        level = (x - 40) * 255 // 159
        lcd.vline(x, 72, 22, rgb(level, 0, 0))
        lcd.vline(x, 100, 22, rgb(0, level, 0))
        lcd.vline(x, 128, 22, rgb(0, 0, level))
    centered(lcd, "dark -> bright", 166)
    lcd.show()
    sleep_ms(1800)

    print("Display: check shapes, readable text and smooth moving dot.")
    start = ticks_ms()
    while ticks_diff(ticks_ms(), start) < 4000:
        elapsed = ticks_diff(ticks_ms(), start)
        lcd.fill(BLACK)
        circle(lcd, 120, 120, 106, CYAN)
        lcd.rect(66, 76, 108, 88, MAGENTA)
        lcd.line(66, 76, 173, 163, BLUE)
        lcd.line(173, 76, 66, 163, BLUE)
        centered(lcd, "PICO 2 W", 96, WHITE, 2)
        centered(lcd, "240 x 240", 128, YELLOW)
        angle = elapsed * math.pi / 1000
        x = 120 + int(90 * math.cos(angle))
        y = 120 + int(90 * math.sin(angle))
        circle(lcd, x, y, 6, GREEN, True)
        lcd.show()
        sleep_ms(15)

    print("Backlight: dim, medium, bright.")
    for brightness in (0.1, 0.4, 1.0):
        lcd.fill(BLACK)
        centered(lcd, "BACKLIGHT", 86, WHITE, 2)
        centered(lcd, "%d%%" % int(brightness * 100), 128, CYAN, 2)
        lcd.show()
        lcd.backlight(brightness)
        sleep_ms(600)
    lcd.backlight(BRIGHTNESS)


def wait_tap(touch):
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
                if ticks_diff(now, press_since) >= TAP_PRESS_MS:
                    point = (sample["raw_x"], sample["raw_y"])
            elif sample["fingers"] == 0 or sample["event"] == 1:
                press_since = None
                if point is not None:
                    if release_since is None:
                        release_since = now
                    elif ticks_diff(now, release_since) >= TAP_RELEASE_MS:
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


def calibrate(lcd, touch):
    targets = ((120, 52), (188, 120), (120, 188))
    print("Touch orientation: hold each target briefly, then lift finger.")
    while True:
        raw_points = []
        for number, (x, y) in enumerate(targets):
            lcd.fill(BLACK)
            centered(lcd, "TOUCH SETUP %d/3" % (number + 1), 92)
            centered(lcd, "Hold target + lift", 144, CYAN)
            circle(lcd, x, y, 14, YELLOW)
            lcd.hline(x - 7, y, 15, WHITE)
            lcd.vline(x, y - 7, 15, WHITE)
            lcd.show()
            point = wait_tap(touch)
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


def target_test(lcd, touch):
    targets = ((120, 120), (120, 48), (192, 120), (120, 192), (48, 120))
    for number, (x, y) in enumerate(targets):
        while True:
            lcd.fill(BLACK)
            centered(lcd, "TARGET TEST %d/5" % (number + 1), 82, CYAN)
            centered(lcd, "Hold cross + lift", 154)
            circle(lcd, x, y, 16, GREEN)
            lcd.hline(x - 8, y, 17, WHITE)
            lcd.vline(x, y - 8, 17, WHITE)
            lcd.show()
            raw_x, raw_y = wait_tap(touch)
            px, py = touch.map_point(raw_x, raw_y)
            print("Target", (x, y), "touch", (px, py))
            if (px - x) ** 2 + (py - y) ** 2 <= 25 * 25:
                break
            print("Missed target: try tapping its center again.")
    print("All five touch targets passed.")
    lcd.fill(BLACK)
    centered(lcd, "5 TARGETS OK", 104, GREEN, 2)
    lcd.show()
    sleep_ms(1200)


def paint_screen(lcd):
    lcd.fill(BLACK)
    centered(lcd, "DRAW WITH FINGER", 32, CYAN)
    lcd.rect(DRAW_X, DRAW_Y, DRAW_W, DRAW_H, CYAN)
    lcd.rect(84, 180, 72, 24, WHITE)
    centered(lcd, "CLEAR", 188)
    centered(lcd, "Ctrl-C to stop", 216)
    lcd.show()


def paint(lcd, touch):
    print("Drawing: drag to paint; tap CLEAR to erase. Ctrl-C to stop.")
    paint_screen(lcd)
    previous = None
    was_pressed = False
    count = 0
    last_refresh = ticks_ms()
    last_touch_log = ticks_ms()
    while True:
        sample = touch.read()
        if sample is not None:
            x, y = sample["x"], sample["y"]
            pressed = sample["pressed"]
            if pressed and not was_pressed:
                count += 1
                if 84 <= x < 156 and 180 <= y < 204:
                    paint_screen(lcd)
            if pressed:
                if (DRAW_X < x < DRAW_X + DRAW_W - 1 and
                        DRAW_Y < y < DRAW_Y + DRAW_H - 1):
                    if previous is not None:
                        lcd.line(previous[0], previous[1], x, y, YELLOW)
                    # Clip the brush at the frame so it cannot erase it.
                    for dy in range(-2, 3):
                        if DRAW_Y < y + dy < DRAW_Y + DRAW_H - 1:
                            dx = int(math.sqrt(4 - dy * dy))
                            left = max(DRAW_X + 1, x - dx)
                            right = min(DRAW_X + DRAW_W - 2, x + dx)
                            lcd.hline(left, y + dy, right - left + 1, YELLOW)
                    previous = (x, y)
                else:
                    previous = None
            else:
                previous = None
            was_pressed = pressed
            lcd.fill_rect(48, 48, 144, 9, BLACK)
            centered(lcd, "x:%03d y:%03d #%d" % (x, y, count), 48, WHITE)
            if pressed and ticks_diff(ticks_ms(), last_touch_log) >= 200:
                print("Touch x=%d y=%d raw=(%d,%d) event=%d gesture=%s" %
                      (x, y, sample["raw_x"], sample["raw_y"], sample["event"],
                       GESTURES.get(sample["gesture"], str(sample["gesture"]))))
                last_touch_log = ticks_ms()
            if sample["gesture"]:
                lcd.fill_rect(56, 164, 128, 9, BLACK)
                centered(lcd, GESTURES.get(sample["gesture"], "unknown"), 164, CYAN)
        elif touch.last_error is not None:
            previous = None
            was_pressed = False
        if ticks_diff(ticks_ms(), last_refresh) >= 40:
            lcd.show()
            last_refresh = ticks_ms()
        sleep_ms(5)


def main():
    board = None
    try:
        board = TouchLCD(baudrate=SPI_BAUDRATE, brightness=BRIGHTNESS, bgr=BGR)
        display_test(board.lcd)
        calibrate(board.lcd, board.touch)
        target_test(board.lcd, board.touch)
        paint(board.lcd, board.touch)
    except KeyboardInterrupt:
        print("Demo stopped.")
    finally:
        if board is not None:
            board.deinit()


if __name__ == "__main__":
    main()
