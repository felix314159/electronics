## Overview

* [Adafruit 5636](https://www.adafruit.com/product/5636)
* 64 MBit Storage from the W25Q64JV
* SPI connection, level-shift supported (3.3v or 5v power and logic). Pins: `VIN`, `3V3`, `GND`, `SCK`, `MISO`, `MOSI` and `CS`
* we can address the storage as flat memory, or we can use filesystems such as littleFS2
* official code available in [Adafruit_SPIFlash](https://github.com/adafruit/Adafruit_SPIFlash)

## Wiring (Pico -> NOR)

* 3V3(OUT) -> VIN
* GND -> GND
* GP18 -> SCK
* GP19 -> MOSI
* GP16 -> MISO
* GP17 -> CS

## Benchmark on pico 2 w using littlefs2

* Write + sync: 0.016 MiB/s in 31.909 seconds
* Read + verification: 0.230 MiB/s in 2.172 seconds