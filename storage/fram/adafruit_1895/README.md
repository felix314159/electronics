## Overview

* [Adafruit 1895](https://www.adafruit.com/product/1895)
* 256Kbit storage, each byte can be read and written directly
* I2C Interface with these pins: `VCC`, `GND`, `WP`, `SCL`, `SDA`, `A2`, `A1` and `A0`
* `WP` pins explained: WriteProtect, when low (0 V) write is allowed, while high (3.3 V) writing is blocked (but read is still possible). if not connected, it acts as if it were low (write allowed)
* The `A2`, `A1` and `A0` pins are used to choose the I2C address in the range of [0x50, 0x57], so if none of them are connected u get `0x50` as address
* fritzing files available online
* Manufacturer ID `0x00A`, product ID `0x510`


## Wiring (FRAM -> Pico 2 W)

* `VCC` -> `3V3(OUT)`
* `GND` -> `GND`
* `SDA` -> `GP4`
* `SCL` -> `GP5`
* `WP`  -> `GP6`


## Benchmark results on Pico 2 W using our micropython driver

| Transfer size | Raw write | Read | Verified write |
|---:|---:|---:|---:|
| 1 byte | 0.42 KiB/s | 0.42 KiB/s | 0.38 KiB/s |
| 16 bytes | 5.32 KiB/s | 5.86 KiB/s | 4.21 KiB/s |
| 64 bytes | 12.81 KiB/s | 16.24 KiB/s | 8.45 KiB/s |
| 256 bytes | 19.23 KiB/s | 28.07 KiB/s | 11.06 KiB/s |
| 1,024 bytes | 21.85 KiB/s | 33.95 KiB/s | 12.54 KiB/s |
| 4,096 bytes | **22.61 KiB/s** | **35.84 KiB/s** | **12.94 KiB/s** |
