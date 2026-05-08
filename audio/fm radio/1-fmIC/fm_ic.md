# Rest of FM Radio - SI4825 FM IC

![final result](./final.png)

We can now build the **FM receiver circuit** for the Europe FM band (FM1, 87–108 MHz, de-emph 50 µs) and connect it
with our already-tested TDA2822 speaker stage.

To connect what we previously built with what we will next build we just create shared ground and use the previous boards `AUDIO_OUT` white cable 
from volume pot pin 3 (which we earlier used to connect to Arduino DAC) and now it instead goes to SI4825 pin 16.

## Parts required for this stage

* 1x SI4825-A10-CSR on SOIC16-to-DIP16 adapter
* 1x 32.768 kHz crystal, 2-pin (make sure to not buy scam product advertised as 32.768 KHz which then turns out to be 32.768 MHz and which makes u debug the circuit for many hours for no reason.., I eventually disassembled an old analog clock to extract a suitable crystal)
* 1x 100 kΩ linear pot for tuning
* 1x antenna wire, about 75-90 cm
* Resistors:
    * 1x 442 kΩ, try to be precise here (i wrote a python program that takes many resistors u own as input and outputs a specific series combination of them to get maximally close to the target value, i only had some cheap resistor kit which was like 10% accurate so I had to get creative)
    * 1x 57.6 kΩ, try to be precise here
    * 3x 100 kΩ
    * 1x 100 Ω
    * 1x 10 Ω
* Caps:
    * 1x 47 µF electrolytic
    * 1x 10 µF electrolytic (or technically 0.47 µF to 4.7 µF is more datasheet aligned, tho matter this does not)
    * 3x 100 nF ceramic
    * 2x 22 pF ceramic

## SI4825 pinout

```
          +---U---+
 LNA_EN  1|       |16  AOUT
  TUNE1  2|       |15  GND
  TUNE2  3|       |14  VDD
   BAND  4|       |13  XTALI
     NC  5|       |12  XTALO
    FMI  6|       |11  VOL-
  RFGND  7|       |10  VOL+
    AMI  8|       |9   RST
          +-------+
```

## SI4825 wiring

| Pin | Name   | Connections |
|-----|--------|-------------|
| 1   | LNA_EN | Not connected |
| 2   | TUNE1  | Wire to Tuning pot pin 3; **and** 442 kΩ to pin 4 `BAND`; **and** 100 nF to GND; **and** 47 µF to GND |
| 3   | TUNE2  | Wire to Tuning pot wiper (middle pin) |
| 4   | BAND   | 57.6 kΩ to GND; **and** 442 kΩ to pin 2 `TUNE1` |
| 5   | NC     | Not connected |
| 6   | FMI    | Antenna wire (straight wire with length 75-90 cm) |
| 7   | RFGND  | Wire to GND |
| 8   | AMI    | Not connected |
| 9   | RST    | 100 kΩ to `VSI`; **and** 100 nF to GND |
| 10  | VOL+   | 100 kΩ to GND |
| 11  | VOL-   | 100 kΩ to GND |
| 12  | XTALO  | Crystal leg 1; **and** 22 pF to GND |
| 13  | XTALI  | Crystal leg 2; **and** 22 pF to GND |
| 14  | VDD    | Wire to `VSI`; **and** 100 nF to GND (very close to the pin) |
| 15  | GND    | Wire to GND |
| 16  | AOUT   | Connect white wire from cap after pin 3 audio pot here |

## Power rails

This stage has two positive rails:

* `VRAW_IN`: incoming 3 V supply from 2x Alkaline battery.
* `VSI`: filtered SI4825 supply after a 10 Ω resistor (so just `VRAW_IN` -> 10 ohm -> `VSI`, so we have two + rails).

When this board is connected to the TDA board, both boards share `VRAW_IN` and
`GND`. The TDA2822 uses `VRAW_IN` directly; the SI4825 gets the quieter `VSI`
rail through the 10 Ω filter resistor.

Note: Add a 10 microF cap between `VRAW_IN` and `GND`

## Tuning pot

100 kΩ linear pot, pins down, viewed from the knob side:

* Pin 1 (left / black): 100 Ω to GND
* Pin 2 (middle / white): SI4825 pin 3 `TUNE2`
* Pin 3 (right / red): SI4825 pin 2 `TUNE1`

Clockwise rotation should tune upward through the band.

Note: If u have pins down and u look at pot knob:
* all the  way to left (maximum counter-clockwise rotation) = 0 ohm between left and middle pin (and 100k ohm between middle and right)
* all the way to right (maximum clockwise rotation) = 100k ohm between left and middle (and 0 ohm between middle and right)
* always 100k ohm between left and right

So the pinout is:
* Pin 1 ('left') = Low side (cable color 'black' makes sense)
* Pin 2 = Wiper (cable color 'white' makes sense)
* Pin 3 ('right') = High side (cable color 'red' makes sense)

## Crystal cluster

The crystal wiring is the most layout-sensitive part of this board:

* Crystal leg 1 -> SI4825 pin 12 `XTALO`
* Crystal leg 2 -> SI4825 pin 13 `XTALI`
* 22 pF from pin 12 to GND
* 22 pF from pin 13 to GND

Place the crystal and both 22 pF caps directly beside pins 12/13. Keep the leads
short. Do not route the antenna wire or tuning pot wires through this area. I guess it makes sense to use the right-side ground rail of the protoboard so that the caps to ground have a shorter path.

## Antenna

Use a straight wire about 75-90 cm long from SI4825 pin 6 `FMI`.

The antenna should stay away from:

* the crystal and 22 pF caps
* the tuning pot wires
* the audio output wire to the TDA stage

and be pointed directly at god in the heavens.

You ask why 90cm ? The wavelength formula is `λ = c / f`, here `c` is the speed of light and `f` the frequency. The European FM radio band center is `(87.5+108) / 2 = 97.75` MHz. So we get `λ = 3*10^8 / 97.75*10^6 = 3.07 meters` wavelength. But it is fine to cut this to a quarter-fraction (technically any **odd-multiple** of λ/4 is fine), so `3.07 / 4 = 0.77` meter is a good length for the antenna. But really anyhing in [0.75, 0.9] m will be fine for this project.

I couldn't really find an easy technical answer to why odd-multiples of `λ/4` work, someone on reddit that then such an antenna 'transforms the near open circuit of a bit of wire into an almost short circuit for the transmitter to dump power into - at wavelengths near `λ`'. Basically at a full-length antenna the electromagnetic wave u pump into it would come back (it can't escape the open-circuit end of the antenna) at a bad point in time (right as we try to send another wave it would be 'blocked/fought' by the wave that is being reflected back [current arrives 180 degrees out of phase, so-called back EMF]). But if u modify the length of the antenna to an odd-multiple of `λ/4`, then we can avoid the wave-clashing and send another them at a time where the new wave and the previous wave reinforce each other and build up more oscillation (more radiation). So it's basically a speed of light / wavelength timing game where we try to have the round-trip travel times of the EM waves match the driving period. In this explanation the antenna was a 'sender', but the same logic in reverse applies for a 'receiver' antenna. For our receiver the exact resonance is not that critical anyway and the antenna sourroundings probably have a bigger impact than its exact length.

## Power

2x Alkaline (3V) works fine.

# Result

I can actually hear music / talking voices (some can pull this off without a radio) but the tuning pot is very sensitive.
Maybe I should have used a 10-turn pot specifically, and also the antenna positioning sometimes switches the selected channel lol.
Basically I can recognize which song is playing / understand some of the words that are said but it is rather quiet on max volume,
a bit of static and basically u never are able to cleanly select one channel because the pot is too sensitive. Was still a fun project tho.
