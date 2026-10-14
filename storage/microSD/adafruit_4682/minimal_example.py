"""Run on the Pico 2 W with driver.py installed and a FAT-formatted microSD card.

LED blinks at 10 Hz while writes happen so that you know it is safe to turn the pico off when nothing is blinking.
"""
import os
import errno
import time
from machine import Pin, Timer
from driver import SDCard

# Some MicroPython builds omit the ENOSPC name from errno.
ENOSPC = getattr(errno, "ENOSPC", 28)

led = Pin("LED", Pin.OUT)
led.on()
print("SD session active; do not power off")


def write_example():
    # The Pico 2 W LED uses the wireless chip, which claims a PIO2 state machine
    # in this firmware. Keep SDIO on its own exclusive PIO0 block instead.
    sd = SDCard(sm_cmd=0, sm_data=1)
    try:
        os.mount(os.VfsFat(sd), "/sd")
        try:
            try:
                # "w": forced overwrite
                with open("/sd/test.txt", "w") as f:
                    f.write("hello world")

                try:
                    os.mkdir("/sd/a")
                except OSError as exc:
                    if exc.args[0] != errno.EEXIST:
                        raise

                with open("/sd/a/test2.txt", "w") as f:
                    f.write("hello world 2")

                # "a": append (if exists) or create
                with open("/sd/test.txt", "a") as f:
                    f.write("\nappended line")

                # "x": create only but never overwrite an existing file
                try:
                    with open("/sd/create_only.txt", "x") as f:
                        f.write("created once")
                    print("Created create_only.txt")
                except OSError as exc:
                    if exc.args[0] != errno.EEXIST:
                        raise
                    print("create_only.txt already exists; left unchanged")

                os.sync()
                print("Files written successfully")
            except OSError as exc:
                if exc.args[0] == ENOSPC:
                    # Demonstrates handling the real error; does not fill the card.
                    # A failed write may leave partial data. There is no rollback.
                    print("SD card is full; a file may have been only partly written")
                    raise  # Let the caller know the operation did not complete.
                raise
        finally:
            os.umount("/sd")
    finally:
        sd.deinit()

# A 50 ms half-period gives 10 complete on/off cycles per second.
# Soft callbacks are required for the wireless-chip-controlled Pico W LED.
blink = Timer()
try:
    blink.init(period=50, mode=Timer.PERIODIC,
               callback=lambda timer: led.toggle(), hard=False)
    write_example()
    # Extend the indication for a full second AFTER all writes and cleanup,
    # even when these small example writes take only a few milliseconds.
    finished_at = time.ticks_us()
    while time.ticks_diff(time.ticks_us(), finished_at) < 1_000_000:
        time.sleep_ms(10)
finally:
    blink.deinit()
    led.on()  # An exception (including Ctrl-C) leaves a steady warning light.

led.off()
print("SD card synced and unmounted; ready to power off")
