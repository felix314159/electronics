"""Safe Adafruit 5636 / W25Q64JV littlefs2 example for a Pico 2 W.

Note: Set FORMAT_NEW_DEVICE to True only once, on a new or deliberately expendable chip, then immediately set it back to False.
"""

import errno
import os

nor = __import__("driver-nor-5636")

# Formatting requires changing both values, reducing the chance that a stale
# True left in a deployed script destroys the next chip's filesystem.
FORMAT_NEW_DEVICE = False
FORMAT_CONFIRMATION = ""
MOUNT_POINT = "/nor"


def main():
    flash = nor.pico2w_flash()
    mounted = False
    try:
        print("NOR:", flash.info())

        if FORMAT_NEW_DEVICE:
            print("FORMATTING: all existing NOR filesystem data will be lost")
            flash.format_lfs2(FORMAT_CONFIRMATION)

        try:
            flash.mount_lfs2(MOUNT_POINT)
            mounted = True
        except OSError:
            if not FORMAT_NEW_DEVICE:
                print("No mountable littlefs2 filesystem.")
                print("For a new chip only, set FORMAT_NEW_DEVICE = True and")
                print("FORMAT_CONFIRMATION = 'ERASE ALL NOR DATA' once.")
            raise

        path = MOUNT_POINT + "/hello.txt"
        try:
            # Create-only mode prevents this demonstration from overwriting an
            # existing file. Close, sync, and read back before reporting success.
            with open(path, "x") as output:
                output.write("Hello from the Adafruit 5636 NOR flash!\n")
            os.sync()
            print("Created", path)
        except OSError as error:
            if not error.args or error.args[0] != errno.EEXIST:
                raise
            print(path, "already exists; leaving it unchanged")

        with open(path, "r") as source:
            print("Read back:", source.read().rstrip())
    finally:
        try:
            if mounted:
                os.sync()
                nor.vfs.umount(MOUNT_POINT)
                print("Filesystem synced and unmounted; it is safe to disconnect power.")
        finally:
            flash.deinit()


if __name__ == "__main__":
    main()

