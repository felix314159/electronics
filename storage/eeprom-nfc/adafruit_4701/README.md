## Overview

* [Adafruit 4701](https://www.adafruit.com/product/4701), basically just an NFC tag (type 5/iso 15693/'v'(icinity)) that you can communicate with via I2C
* 2 KB (16 Kbits) EEPROM storage, uses st25dv16 nfc tag, data is organized as 512 blocks each of which holds 4 byte
* the tag has an 8-byte UID
* technically also has a 256 byte of volatile RAM (half-duplex 256-byte dedicated buffer used in fast transfer mode), which u can use via special ST fast-transfer APDU commands. this storage is memory-mapped in [0x2008, 0x2107]. u can only use this while actively powering the device (e.g. via a pico) via VCC (rf field alone does not provide enough power), and it exists so that u can avoid wearing out the EEPROM for frequent write applications
* I2C Pins: `VIN`, `GND`, `SCL`, `SDA`, `GPO` and `VEH`
* most modern phones should be able to read the tag, but some readers like e.g. the acr122u (contains pn532) will not be able to read it cuz they don't support ISO 15693. i recommend nxp's taginfo app

* [Official guide](https://learn.adafruit.com/adafruit-st25dv16k-i2c-rfic-eeprom-breakout)
* I2C interface accesses bytes
* RF interface accesses blocks of 4 bytes
* Data Protection
    * User memory: 1 to 4 configurable areas, protectable in read and/or write by three 64-bit passwords in RF and one 64-bit password in I2C
    * System configuration: protected in write by a 64-bit password in RF and a 64-bit password in I2C

* A cool follow-up project would be use `VEH` to power some super-low power component (e.g. small cap) and use it for sth else entirely

## Wiring (Pico 2 W -> 4701)

* `3V3(Out)` -> `VIN`
* `GND` -> `GND`
* `GP4` -> `SDA`
* `GP5` -> `SCL`
* `GPO` [digital notification/nfc-event output] and `VEH` [harvested energy voltage output, basically the energy we received from an NFC reader] are **not connected**.

## Performance (Adafruit Claims)

* Write time:
    * From I2C: typical 5ms for 1 byte
    * From RF: typical 5ms for 1 block (which is 4 bytes)
* Write cycles endurance:
    * 1 million write cycles at 25 °C
    * 600k write cycles at 85 °C
    * 500k write cycles at 105 °C
    * 400k write cycles at 125 °C
