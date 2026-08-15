## Part Used

* [W5500 SPI Ethernet Modul](https://www.berrybase.de/w5500-spi-ethernet-modul)

## Compile Micropython with W5500 support (stock micropython does not work)

* `sudo apt update`

* `sudo apt install -y build-essential cmake git gcc-arm-none-eabi libnewlib-arm-none-eabi`

* `pip3 install mpremote --break-system-packages`

* `cd /home/user/Downloads`

* `git clone --depth 1 --branch v1.28.0 https://github.com/micropython/micropython.git micropython-w5500`

* `cd /home/user/Downloads/micropython-w5500`

* `make -C mpy-cross -j"$(nproc)"`

* `cd ports/rp2`

* `CMAKE_ARGS="-DMICROPY_PY_NETWORK_WIZNET5K=W5500" make BOARD=RPI_PICO2_W submodules`

* `make BOARD=RPI_PICO2_W clean`

* `CMAKE_ARGS="-DMICROPY_PY_NETWORK_WIZNET5K=W5500" make -j"$(nproc)" BOARD=RPI_PICO2_W`

* `mpremote connect /dev/ttyACM0 bootloader`

* `cp build-RPI_PICO2_W/firmware.uf2 /media/user/RP2350`

## Run Program on Pico 2 W

```bash
mpremote connect /dev/ttyACM0 fs cp main.py :main.py && \
mpremote connect /dev/ttyACM0 fs cp w5500.py :w5500.py && \
mpremote connect /dev/ttyACM0 reset && \
sleep 1 && \
mpremote connect /dev/ttyACM0
```