## Adafruit INA219 Project

Notes about INA219 current sensor breakout:
- https://learn.adafruit.com/adafruit-ina219-current-sensor-breakout/downloads
- 0-26 V bus voltage supported
- +- 3.2 A supported
- I2C Address: 0x40
- for me 1 mA resolution is good enough (default is 0.8 mA)
- note: the vin- and vin+ duplicates (screw terminal and i2c pins) are the same pins, you can use either ones

---

For our project we want the value to be displayed somewhere, so I have:
* as display i will be using the [crowtail 4 digit display v2.0](https://www.elecrow.com/wiki/crowtail--4-digit-display.html) with four wires (vcc, ground, data and clock). this display is based on tm1650 driver
* as microcontroller (take data from ina219 and display it on display) i will be using an attiny85
* for power we will use 4.5 - 5v battery

## Wiring

### INA219

* VCC pin:              wire to battery-side + breadboard rail
* GND pin:              wire to breadboard gnd rail
* SCL pin:              wire to attiny pin 7
* SDA pin:              wire to attiny pin 5
* VIN + pin:            wire to battery-side + breadboard rail
* screw-terminal Vin +: \<unused\> (we use the vin+ pin which is the same node anyway)
* screw-terminal Vin -: connect this to the + rail of the circuit we want to monitor current usage of

Note: The battery - is connected to the gnd rail of the circuit we want to monitor current usage of

Note 2: The naming of 'vin -' is very bad, it is **not** battery minus and it is **not** ground

### ATtiny

* Pin 1: \<unused\>
* Pin 2: Display CLK (yellow wire)
* Pin 3: Display DATA (white wire)
* Pin 4: Breadboard GND rail (shared by everything: Battery -, ina219 gnd, display gnd, measured circuit -)
* Pin 5: INA219 SDA (white wire)
* Pin 6: \<unused\>
* Pin 7: INA219 SCL (yellow wire)
* Pin 8: wire to battery-side + rail

### 5V battery

* Battery +: connect to battery-side + breadboard rail
* Battery -: shared GND rail, which is also connected to the target circuit GND rail (so we directly both circuit ground rails together and then it doesn't matter which one the battery - is connected to)

### Breadboard Rails

we have two + rails:

1. battery-side + rail [i use right side of breadboard]: 
    * battery +
    * attiny pin 8
    * ina219 vcc
    * ina219 vin+
    * display vcc 

2. load-side + rail [i use left side of breadboard]:
    * ina219 vin- [one of the two screw terminals]
    * '+' wire that powers the circuit we are testing


TLDR: the measured current path is: `battery + rail -> INA219 VIN+ -> shunt -> INA219 VIN- -> load-side + rail -> tested circuit -> GND rail -> battery -`
and everything we need to do those measurements it powered from the non-measured side so that it does not effect the results

The display will show the mA current value measured (4 digits means up to 9999 mA could be shown in theory, but realistically we are limited to 3200 mA due to the ina219)

## Verifying the circuit works

Use 440 ohm resistor and green led as target circuit, as power source use 3x aa alkaline in series. You should see around 6 mA on the display