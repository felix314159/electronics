"""Minimal W5500 Ethernet test for the Pico 2 W."""

import socket
import time

from machine import Pin, SPI
from w5500 import connect_ethernet


HTTP_HOST = "example.com"
HTTP_PORT = 80
HTTP_PATH = "/"


def check_w5500_spi():
    """Read the W5500 VERSIONR register: a real W5500 returns 0x04. sth like 0x00 indicates an error (e.g. spi not found)"""
    cs = Pin(13, Pin.OUT, value=1)
    reset = Pin(14, Pin.OUT, value=0)
    time.sleep_ms(20)
    reset.value(1)
    time.sleep_ms(100)

    spi = SPI(
        1,
        baudrate=1_000_000,
        polarity=0,
        phase=0,
        sck=Pin(10),
        mosi=Pin(11),
        miso=Pin(12),
    )
    tx = bytearray((0x00, 0x39, 0x00, 0x00))
    rx = bytearray(4)
    cs.value(0)
    spi.write_readinto(tx, rx)
    cs.value(1)

    version = rx[3]
    print("W5500 VERSIONR: 0x{:02x}".format(version))
    if version != 0x04:
        raise RuntimeError(
            "W5500 did not answer SPI; check 3.3 V, ground, SCK, MO, MI, CS, and RST"
        )


def fetch_text():
    """Fetch and print a small static HTTP page using the W5500."""
    address = socket.getaddrinfo(HTTP_HOST, HTTP_PORT)[0][-1]
    sock = socket.socket()
    sock.settimeout(10)

    try:
        print("Connecting to {}...".format(address))
        sock.connect(address)

        request = (
            "GET {} HTTP/1.0\r\n"
            "Host: {}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).format(HTTP_PATH, HTTP_HOST)
        sock.sendall(request.encode())

        print("----- HTTP response -----")
        while True:
            chunk = sock.recv(512)
            if not chunk:
                break
            print(chunk.decode("utf-8", "replace"), end="")
        print("\n----- end response -----")
    finally:
        sock.close()


# Keep boot messages visible when mpremote reconnects after a board reset.
time.sleep(3)

check_w5500_spi()
ethernet = connect_ethernet()
print("W5500 Ethernet is working.")
fetch_text()
