# Pico 2 W:
#     pin 36 (3v3 out) has 0.25 a resettable fuse [also called PPTC, i use MF-R025] to breadboard + rail 
#     pin 38 (ground) to breadboard gnd rail 
#     pin 29 (gpio22) to row x 
# 
#     pin 21 (i2c data)
#     pin 22 (i2c clock)
#   How to power the pico? 3x AA alkaline lasted only around 36 hours for me, so now i use the official rpi micro usb charger (12.5 W). it has short protection and we also use a fuse, and i also covered everything in kapton so it should be fine
# --------------------------------------------------------------- 
# dht11 humidity sensor (4 legs): 
#     you look at the side with the many small squares, pins go down:
#       pinout: pin 1: vcc, pin 2: data, pin 3: NC, pin 4: gnd
# 
#     pin 1 goes to breadboard + rail
#     pin 2 is connected directly to pin 29 of pico
#     pin 2 also has 10k ohm resistor to breadboard + rail
#     pin 3 is not connected
#     pin 4 goes to breadboard gnd rail
# --------------------------------------------------------------- 
# tmp117 (temp sensor), 7-bit I2C address is 0x48:
#     SDA goes to row sda
#     SCL goes to row scl
#     VIN goes to breadboard + rail
#     GND goes to breadboard gnd rail
# --------------------------------------------------------------- 
# scd41 (co2 sensor), 7-bit I2C address is 0x62:
#     SDA goes to row sda
#     SCL goes to row scl
#     VIN goes to breadboard + rail
#     GND goes to breadboard gnd rail
# Note: do NOT touch the white 'sticker' looking thing on the sensor, it is a protection membrane. it is NOT a removable sticker like on some active buzzers to make them more quiet lol

from machine import Pin, I2C
import dht
import network
import time
import socket
import ujson
from math import exp

# -----------------------------
# WiFi / server config
# -----------------------------
SSID = "<yourWifiNetworkName>"
WIFI_PASS = "<yourWifiPassword>"
SERVER_HOST = "<yourServerIP>"

DEVICE_NAME = "pico-2-w-1"
SERVER_PORT = 8234
SERVER_PATH = "/"

# -----------------------------
# DHT11 on GPIO22
# -----------------------------
dht_sensor = dht.DHT11(Pin(22))

# -----------------------------
# I2C bus
# -----------------------------
i2c = I2C(0, sda=Pin(16), scl=Pin(17), freq=100000)

# -----------------------------
# TMP117
# -----------------------------
TMP117_ADDR = 0x48
TMP117_TEMP_REG = 0x00

def read_tmp117_celsius():
    data = i2c.readfrom_mem(TMP117_ADDR, TMP117_TEMP_REG, 2)
    raw = (data[0] << 8) | data[1]

    if raw & 0x8000:
        raw -= 0x10000

    return raw * 0.0078125


# -----------------------------
# SCD41
# -----------------------------
SCD41_ADDR = 0x62

def scd4x_crc(data_bytes):
    crc = 0xFF
    for byte in data_bytes:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc

def scd41_send_command(command):
    cmd = bytes([(command >> 8) & 0xFF, command & 0xFF])
    i2c.writeto(SCD41_ADDR, cmd)

def scd41_start_periodic_measurement():
    scd41_send_command(0x21B1)

def scd41_read_measurement():
    scd41_send_command(0xEC05)
    time.sleep_ms(1)

    data = i2c.readfrom(SCD41_ADDR, 9)

    if scd4x_crc(data[0:2]) != data[2]:
        raise ValueError("SCD41 CRC error on CO2 word")
    if scd4x_crc(data[3:5]) != data[5]:
        raise ValueError("SCD41 CRC error on temperature word")
    if scd4x_crc(data[6:8]) != data[8]:
        raise ValueError("SCD41 CRC error on humidity word")

    co2_raw = (data[0] << 8) | data[1]
    temp_raw = (data[3] << 8) | data[4]
    rh_raw = (data[6] << 8) | data[7]

    co2_ppm = co2_raw
    temp_c = -45.0 + 175.0 * (temp_raw / 65535.0)
    rh_percent = 100.0 * (rh_raw / 65535.0)

    return co2_ppm, temp_c, rh_percent


# -----------------------------
# Absolute humidity formula
# -----------------------------
def absolute_humidity_gm3(temperature_c, relative_humidity_percent):
    rh = relative_humidity_percent / 100.0
    abs_humidity = (
        (
            6.11
            * exp((17.67 * temperature_c) / (243.5 + temperature_c))
            * rh
            * 2.1674
        )
        / (273.15 + temperature_c)
    ) * 100.0
    return abs_humidity


# -----------------------------
# WiFi helpers
# -----------------------------
wlan = network.WLAN(network.STA_IF)

def connect_wifi():
    if wlan.isconnected():
        return

    while True:
        wlan.active(True)
        wlan.connect(SSID, WIFI_PASS)

        print("Connecting to", SSID, end="")
        for _ in range(5):
            if wlan.isconnected():
                break
            print(".", end="")
            time.sleep(1)

        if wlan.isconnected():
            break

        print(" timed out, resetting WiFi...")
        wlan.disconnect()
        wlan.active(False)
        time.sleep(1)

    print()
    print("Connected! IP:", wlan.ifconfig()[0])

def ensure_wifi():
    if not wlan.isconnected():
        print("WiFi disconnected, reconnecting...")
        connect_wifi()


# -----------------------------
# Raw HTTP POST helper
# -----------------------------
def post_measurement(co2_ppm, temperature, humidity):
    payload = {
        "device": DEVICE_NAME,
        "co2_ppm": str(co2_ppm),
        "temp_c": "{:.2f}".format(temperature),
        "humid_rel_perc": str(humidity),
    }

    body = ujson.dumps(payload)
    body_bytes = body.encode("utf-8")

    addr = socket.getaddrinfo(SERVER_HOST, SERVER_PORT)[0][-1]
    s = socket.socket()

    try:
        s.connect(addr)

        request = (
            "POST {} HTTP/1.1\r\n"
            "Host: {}:{}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: {}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).format(SERVER_PATH, SERVER_HOST, SERVER_PORT, len(body_bytes))

        s.send(request.encode("utf-8"))
        s.send(body_bytes)

        response = s.recv(1024).decode("utf-8", "ignore")
        status_ok = "200" in response.split("\r\n")[0]

        if status_ok:
            print("-> Successfully sent data to server:")
            print(payload)
        else:
            print("-> Server error:")
            print(response)
            print("Payload was:", payload)

    finally:
        s.close()


# -----------------------------
# Startup
# -----------------------------
connect_wifi()

try:
    scd41_start_periodic_measurement()
    time.sleep(5)
except Exception as e:
    print("SCD41 startup failed:", e)


# -----------------------------
# Main loop
# -----------------------------
while True:
    try:
        ensure_wifi()

        dht_sensor.measure()
        humidity = dht_sensor.humidity()

        temperature = read_tmp117_celsius()

        co2_ppm, _, _ = scd41_read_measurement()

        abs_humidity = absolute_humidity_gm3(temperature, humidity)

        print("Temp: {:.2f} C".format(temperature))
        print("RH: {} %".format(humidity))
        print("Absolute humidity: {:.2f} g/m^3".format(abs_humidity))
        print("CO2: {} ppm".format(co2_ppm))
        post_measurement(co2_ppm, temperature, humidity)

    except OSError as e:
        print("Read failed:", e, "- server at {}:{} may be down".format(SERVER_HOST, SERVER_PORT))
    except Exception as e:
        print("Error:", e)

    print()
    time.sleep(5)
