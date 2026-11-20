# Gameboy USB-C power (tested with 'Advance SP' and 'DS')

First fully remove the battery packs that the devices came with. Then solder two wires directly to the terminals and ensure they can't be shorted by moving them.
Then remember to never use the gameboy's charging port while we do the following.

## USB-C breakout

My USB-C breakout does not have any resistors. So I had to add **CC1 → 5.1 kΩ → GND** and **CC2 → a separate 5.1 kΩ → GND**. We only care about `VCC`, `CC1`, `CC2` and `GND`, the other pins can be ignored. It's also a good idea to put a 1.25 A fuse (e.g. Littelfuse 02511.25MAT1L) close to VCC, the gameboys won't trip this one.
Use any USB-C phone charger with a USB-C wire to this port and you get stable 5V. In case you are wondering how much current your charger can provide at 5V in this config: While connected to the breakout just measure the voltage between `CC1` and `GND` (or `CC2` to `GND`):

* around 0.4 V would mean you 'default USB current' so around up to 500 mA.
* around 0.9 V would mean up to 1.5 A
* around 1.7 V would mean up to 3 A


## Adafruit TPS62827 (product [4920](https://www.adafruit.com/product/4920))

* Valid input range is [3.4V,5.5V]
* If the header pins are at the bottom, then look at the top left of the board and you will see three horizontal parts:

    * Top: Cap C4 (120 pF)
    * Middle: **R2 (by default 453 kOhm, as shown by EIA-96 resistor code '64D')**
    * Bottom: R3 (100k ohm)

R2 and R3 make up a voltage divider, the board will always adjust the output so that there is 0.6V at the `FB` (feedback) pin.
So we can just replace **R2** with a different resistor to get output voltages other than 3.3V. Here is a table of possible mods:

| R2 Value | Output Voltage |
| --- | --- |
| 0 Ω | 0.6 V |
| 68 kΩ | 1 V |
| 100 kΩ | 1.2 V |
| 150 kΩ | 1.5 V |
| 200 kΩ | 1.8 V |
| 316 kΩ | 2.5 V |
| **453 kΩ (default)** | **3.3 V** |
| 510 kΩ | 3.66 V |
| 560 kΩ | 3.96 V |

We need to stay in [0.6V,4V] output because the [TPS62827 datasheet](https://www.ti.com/lit/ds/symlink/tps62827.pdf) says thats the adjustable output voltage range.
The closer you get to 4V output the more important it is to be accurate with your replacement R2 (and ideally stay at or slightly above 5V input when u do that). This mod does not affect how much current generally can be pushed through this device (up to 2 A according to adafruit).

---

For this project we take our stable USB-C 5V (which you get even without any PD controller) and convert it to stable 3.7V. Since the Adafruit by default outputs 3.3V we have to mod it: Solder a **510 kΩ** resistor instead of the default 453 kΩ resistor. This works for both the Nintendo Advance SP and the Nintendo DS (first gen).

# Result

It works well! Not sure why the battery indicator LED of the gameboy DS is red, but it can be ignored.