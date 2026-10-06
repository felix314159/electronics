## Circuit

Potentiometer allows user to dim an LED. We use a 555 timer to generate PWM signal (quickly turning LED on/off to save power compared to using a dumb pot circuit that always wastes battery charge as heat).

## Parts

* 6V Battery (4*AA Alk)
* Red LED
* NE555 Timer (i don't have TLC CMOS version, so instead of coincell we will be using AA batteries)
* 2x 1N4148 diodes

* 100 k Ohm linear pot
* 2x 1k ohm res (to prevent timing resistance reaching zero at end of pot)
* 330 Ohm LED res

* 10 microF cap
* 100 nF cap (across 555 supply)
* 2x 10 nF cap (one for timing, one for pin5 to gnd)


### Timer Pinout

* Pin 1: GND
* Pin 2: Trigger
* Pin 3: Output
* Pin 4: Reset
* Pin 5: Control Voltage
* Pin 6: Threshold
* Pin 7: Discharge
* Pin 8: VCC

## Wiring

0. Solder Pot

1. Solder 555 timer
    * Connect Pin 1 to  `-` rail
    * Connect Pin 8 to `+` rail
    * Connect Pin 4 to `+` rail
    * 100 nF cap between Pin 8 and Pin 1
    * 10 nF cap between Pin 5 and `-` rail
    * Wire Pin 2 to Pin 6 (our timing node `T`)
    * 10 nF cap between `T` and `-` rail
    * 1k ohm res between `+` rail and pin 7
2. 10 microF cap between `+` and `-` rails

3. 1N4148 diode between Pin 7 and pot terminal `A` (outer) (unstriped end to Pin 7, striped end to the pot)
4. Second 1N4148 diode between pot terminal `B` (outer) and Pin 7 (striped end to Pin 7, unstriped end to terminal B)
5. 1k ohm res between the pot wiper and `T`

6. Solder LED
    * 330 Ohm res between `+` rail and LED anode
    * connect the LED cathode to Pin 3

7. Solder the 2-female dupont for the battery connection / or use a screw terminal

## Result

Works well and feels fairly linear. However, at lowest brighness settings the pot is fairly sensitive.