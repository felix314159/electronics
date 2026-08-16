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


class RadioDisableError(RuntimeError):
    """Raised when an onboard radio cannot be confirmed inactive."""


def _interface_id(current_name, legacy_name):
    """Return a WLAN interface ID across current and older APIs."""
    wlan_class = getattr(network, "WLAN", None)
    interface_id = getattr(wlan_class, current_name, None)
    if interface_id is None:
        interface_id = getattr(network, legacy_name, None)
    return interface_id


def disable_onboard_radios():
    """Disable the Pico W station/AP Wi-Fi interfaces and Bluetooth LE."""
    wlan_class = getattr(network, "WLAN", None)
    if wlan_class is not None:
        station_id = _interface_id("IF_STA", "STA_IF")
        ap_id = _interface_id("IF_AP", "AP_IF")
        interface_ids = (station_id, ap_id)

        for interface_id in interface_ids:
            if interface_id is None:
                continue
            wlan = wlan_class(interface_id)
            wlan.active(False)
            if wlan.active():
                raise RadioDisableError("Wi-Fi interface remained active")

    try:
        import bluetooth
    except ImportError:
        # A firmware built without Bluetooth has no BLE radio API to disable.
        bluetooth = None

    if bluetooth is not None:
        ble = bluetooth.BLE()
        ble.active(False)
        if ble.active():
            raise RadioDisableError("Bluetooth LE radio remained active")

    print("Onboard Wi-Fi and Bluetooth disabled")


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


def connect_ethernet(ethernet=None, hostname=None):
    """Activate Ethernet and retry until link and an IPv4 address are ready."""
    disable_onboard_radios()

    if hostname is not None:
        set_hostname = getattr(network, "hostname", None)
        if set_hostname is not None:
            set_hostname(hostname)

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
