"""Adafruit 4701 + Pico 2 W demo.

This demo formats the full EEPROM as a standards-compliant Type 5 NDEF area and writes a URL.
It replaces any existing EEPROM content.
"""

driver = __import__("driver-eeprom-nfc-4701")

tag = driver.ST25DV16K.from_pico(i2c_id=0, sda=4, scl=5, freq=400_000)

print("I2C scan:", ["0x%02X" % address for address in tag.scan()])
info = tag.device_info()
print("Model:", info["model"])
print("EEPROM bytes:", info["capacity_bytes"])
print("ISO 15693 UID:", info["uid"])

ndef_area_bytes = tag.format_ndef()
print("NDEF Type 5 area bytes:", ndef_area_bytes)

demo_url = "https://www.raspberrypi.com/documentation/microcontrollers/micropython.html"
tag.write_ndef_uri(demo_url)
print("NDEF URL written and verified:", tag.read_ndef_uri())
