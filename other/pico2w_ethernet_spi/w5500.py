"""W5500 Ethernet setup for the Pico 2 W air-quality monitor.

This module deliberately uses MicroPython's native network.WIZNET5K driver.
That driver integrates the W5500 with MicroPython's socket module and supplies
the DHCP, DNS and TCP/IP implementation.
"""

from machine import Pin, SPI
import network
import time


SPI_ID = 1
SPI_BAUDRATE = 10_000_000
SCK_PIN = 10
MOSI_PIN = 11
MISO_PIN = 12
CS_PIN = 13
RESET_PIN = 14

CONNECT_TIMEOUT_SECONDS = 20
RETRY_DELAY_SECONDS = 2


class W5500FirmwareError(RuntimeError):
    """Raised when the installed MicroPython build has no W5500 driver."""


def create_ethernet():
    """Create the W5500 network interface for this project's fixed wiring."""
    driver = getattr(network, "WIZNET5K", None)
    if driver is None:
        raise W5500FirmwareError(
            "This MicroPython firmware has no network.WIZNET5K driver. "
            "Install a Pico 2 W build compiled with "
            "MICROPY_PY_NETWORK_WIZNET5K=W5500."
        )

    spi = SPI(
        SPI_ID,
        baudrate=SPI_BAUDRATE,
        polarity=0,
        phase=0,
        sck=Pin(SCK_PIN),
        mosi=Pin(MOSI_PIN),
        miso=Pin(MISO_PIN),
    )

    return driver(spi, Pin(CS_PIN), Pin(RESET_PIN))


def _start_dhcp(ethernet):
    """Start DHCP across current and older MicroPython network APIs."""
    try:
        ethernet.ipconfig(dhcp4=True)
        return
    except (AttributeError, TypeError, ValueError):
        pass

    # Older WIZNET5K builds expose DHCP through ifconfig("dhcp").
    try:
        ethernet.ifconfig("dhcp")
    except (AttributeError, TypeError, ValueError):
        # Some lwIP builds start DHCP automatically when active(True) is called.
        pass


def ip_address(ethernet):
    """Return the current IPv4 address across MicroPython API versions."""
    try:
        address = ethernet.ipconfig("addr4")
        if isinstance(address, tuple):
            return address[0]
        return address
    except (AttributeError, TypeError, ValueError):
        return ethernet.ifconfig()[0]


def connect_ethernet(ethernet=None):
    """Activate Ethernet and retry until link and an IPv4 address are ready."""
    if ethernet is None:
        ethernet = create_ethernet()

    while True:
        try:
            if not ethernet.active():
                ethernet.active(True)
                _start_dhcp(ethernet)

            print("Waiting for Ethernet link and DHCP", end="")
            for _ in range(CONNECT_TIMEOUT_SECONDS):
                if ethernet.isconnected():
                    print()
                    print("Ethernet connected! IP:", ip_address(ethernet))
                    return ethernet
                print(".", end="")
                time.sleep(1)

            print(" timed out, resetting W5500...")
        except OSError as error:
            print()
            print("W5500 connection error:", error)

        try:
            ethernet.active(False)
        except OSError:
            pass
        time.sleep(RETRY_DELAY_SECONDS)


def ensure_ethernet(ethernet):
    """Return once Ethernet is connected, reconnecting when necessary."""
    if ethernet.isconnected():
        return ethernet

    print("Ethernet disconnected, reconnecting...")
    return connect_ethernet(ethernet)

