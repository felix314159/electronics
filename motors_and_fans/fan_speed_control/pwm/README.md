## Overview

Simple circuit to power a 5v 4-wire PWM fan (like you would use in a laptop) and set its speed via a pot.

## Parts

* 220 microF cap
* 2x 100 nF cap

* 100 kΩ resistor
* 4.7 kΩ resistor

* 10 kΩ linear Pot

* Attiny85

* Fan (Sunon Maglev MF600-70V1-C370-S9A, 5V, 2.25W):
    * Black wire
    * Red wire
    * Blue wire
    * Yellow wire

* BC547 transistor (switches only the fan PWM control signal)

* around 5V power (3x aa alkaline battery is fine)

## Attiny Firmware

* Burn the bootloader to guarantee 8 MHz internal clock source + millis enabled

## Wiring

### ATtiny85

* Pin 4:  `GND` rail
* Pin 6:  4.7 kΩ resistor to BC547 `base`
* Pin 7:  wire to pot wiper
* Pin 7:  100 nF cap to `GND` rail
* Pin 8:  `+` rail
* Pin 8:  100 nF cap to pin 4

### Potentiometer

* outer leg 1:  `+` rail
* wiper:        ATtiny Pin 7
* outer leg 2:  `GND` rail

### Transistor (BC547)

* base:         (already connected to ATtiny Pin 6 through the 4.7 kΩ resistor)
* base:         100 kΩ resistor to `GND` rail
* emitter:      `GND` rail
* collector:    `pwm` fan wire [blue wire]

### Fan (Sunon MagLev MF60070V1)

* red:      `+` rail
* black:    `GND` rail
* blue:     BC547 collector         [pwm control]
* yellow:   not connected           [tachometer / FG output not needed here]

### Capacitors

* (we already put a 100 nF cap between attiny pins 4 and 8; we also already put one between pin 7 and ground)
* 220 µF between `+` rail (positive lead) and `GND` rail, at least 10 v voltage rating

## Result

Works very well, feels perfectly linear and works smooth even on very slow settings. On this specific fan model it turns completely off at the very lowest Pot setting like it should, but that might not be guaranteed on other fan models.