## Overview

Simple circuit for controlling two small servo motors with a Pico 2 W.

## Parts

* 2x Soldered SG90 Servo Motor
* Pico 2 W + 5.1 V / 2.5 A Power supply (or USB connection)
* TC4427CPA Dual MOSFET Driver

* 2x 10k Ohm Resistor
* 2x 220 Ohm Resistor

* 1000 µF electrolytic cap, rated ≥10 V
* 3x 100 nF ceramic cap

* Fuse MFR110 (1.1 A, PPTC)


## Wiring

| Connection | Wire to |
| --- | --- |
| Pico **VBUS** | Fuse to `+` rail of breadboard |
| `+` rail of breadboard | Connect both servo **red** wires and TC4427 **pin 6 (VDD)** |
| Pico **GND, physical pin 38** | Local ground junction; both servo **brown** wires and TC4427 **pin 3 (GND)** |
| Pico **GP14, physical pin 19** | TC4427 **pin 2 (IN A)** |
| Pico **GP15, physical pin 20** | TC4427 **pin 4 (IN B)** |
| TC4427 **pin 7 (OUT A)** | Through **220 ohm** to `MOTOR_A` (left) **yellow signal** |
| TC4427 **pin 5 (OUT B)** | Through **220 ohm** to `MOTOR_B` (right) **yellow signal** |
| TC4427 pins **2 and 4** | Each through its own **10 kohm** resistor to ground (keeps signals low during reset) |
| TC4427 pins **1 and 8 (NC)** | Leave unconnected |
| **100 nF ceramic** | Directly between TC4427 pins **6 and 3**, with very short leads |
| **1000 µF electrolytic, rated ≥10 V** | At servo power junction: **+ to +5 V, − to ground** |
| **100 nF ceramic per servo** | Across red/brown at each servo connector |

### TC4427 Pinout

* Pin 1: NC
* Pin 2: INPUT A
* Pin 3: GND
* Pin 4: INPUT B
* Pin 5: OUTPUT B
* Pin 6: VDD
* Pin 7: OUTPUT A
* Pin 8: NC

## Result

Works well, but it lacks a sense of 'start calibration' and 'can i move in this direction without destroying this servo'. It also is difficult to physically mount the servos.