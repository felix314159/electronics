## MicroPython Driver for Adafruit 4682 - Micro SD Breakout

* Uses SDIO (Secure Digital Input Output) which is supported by the Pico 2 W, it is much faster (4 bi-directional data lines) than SPI (2 one-directional data lines). Basically with SDIO you get 4 bits per clock, but in SPI mode you would only get 1 bit per clock per direction, so SDIO has 4x raw payload bandwidth
* Filesystem: FAT32 (so max filesize is 4 GiB - 1 byte)
* Supports microSD cards up to 2 TB (but I only tested it with 128 GB because I am POOR)
* Benchmarks on Pico 2 W showed (try to use short wires when soldering):
    * Raw 192 KiB bursts: 13 MB/s read, 8 MB/s write
    * FAT32 files: 5.5 MB/s read, 4.5 MB/s write

## Examples

* `driver.py`: The driver for microSD module
* `minimal_example.py`: Simple real-world example of how to use the driver to write files to your sd card (needs `driver.py`)
* `bench_sdio.py`: Benchmarking script (needs `driver.py`)

## Wiring

### MicroSD Module Pinout

* Pin 1: `3V` [they mean 3.3V]
* Pin 2: `GND`
* Pin 3: `CLK`
* Pin 4: `D0/SO` [Data 0, Serial Out]. In SPI mode this is one-directional (card-to-pico) `MISO`, but in SDIO this is bi-directional
* Pin 5: `CMD/SI` [In SPI mode this is `MOSI`, in SDIO mode this is commands and responses]
* Pin 6: `D3/CS` [In SPI mode this is chip select, in SDIO mode this is a the bidirectional data line D3]
* Pin 7: `D1`
* Pin 8: `DAT2` [adafruit what are you doing, call this `D2` please]
* Pin 9: `DET` [optional card-presence detection, HIGH when a card is inserted]

### Pico 2 W Pin -> MicroSD Pin Connections

* `3V3(OUT` -> `3V`
* `GND` -> `GND`
* `GP10` -> `CLK`
* `GP12` -> `D0/SO`
* `GP11` -> `CMD/SI`
* `GP15` -> `D3/CS`
* `GP13` -> `D1`
* `GP14` -> `DAT2`
* `DET` is not connected
