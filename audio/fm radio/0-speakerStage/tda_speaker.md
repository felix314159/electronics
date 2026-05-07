# Stage 0 — TDA2822 + Speaker + Volume Pot (driven by Arduino)

## Goal of this stage

Build and verify the **audio output stage** of the FM radio in isolation, before adding any RF circuitry.

## What this stage proves

* TDA2822M is wired correctly in BTL mode (no oscillation, no DC on the speaker, both halves of the bridge alive).
* The Zobel networks across each output keep things stable into the 8 Ω LSM-70A.
* VCC bypass (100 µF) is sufficient — no audible motorboating on loud signals.
* The 10 kΩ audio-taper pot is wired the right way round (clockwise = louder).
* Common-ground discipline between an external source and the amp board works.

## Parts required for this stage

* 1× TDA 2822M
* 1× LSM-70A speaker (8 Ω, 0.5 W nominal)
* 1× 10 kΩ audio/log taper pot (volume)
* Caps:
    * 1× 100 µF electrolytic (VCC bulk bypass)
    * 1× 10 µF electrolytic (Arduino → pot AC coupling)
    * 1× 10 µF electrolytic (BTL feedback OUT2 → NF1)
    * 4× 100 nF
* Resistors:
    * 1× 10 kΩ (input bias on pin 7)
    * 2× 4.7 Ω (or 4× 10 Ω as 2 parallel pairs) — Zobel networks
* 3 wires to Arduino Giga (see below)
* Arduino Giga R1 (uses its 12-bit DAC on pin A12)


## The 3 wires to the Arduino Giga

| Wire | Arduino Giga pin | Goes to                                  | Purpose                          |
|------|------------------|------------------------------------------|----------------------------------|
| 1    | `3V3`            | VRAW rail (= TDA pin 2)                  | Supply for TDA                   |
| 2    | `GND`            | GND rail (= TDA pin 4 + speaker return)  | Common reference                 |
| 3    | `A12` (DAC0)     | `+` of a 10 µF cap; `−` to pot pin 3     | AC-coupled audio signal in       |

The 10 µF on wire 3 is mandatory — the Giga's DAC sits around 1.65 V DC at idle, and we don't want that DC reaching the pot or the TDA input.

## Volume Pot (10 kΩ audio/log) — same as final design

Pins-down, viewed from the knob side:

* Pin 1 (left, black wire):  GND
* Pin 2 (middle, wiper, white wire):  TDA pin 7
* Pin 3 (right, red wire):  `−` leg of the 10 µF coupling cap (`+` leg is on Arduino DAC0)

Clockwise rotation = louder.

## TDA2822M wiring

```
        ┌───U───┐
 OUT1  1│       │8  NF1  (Input 1 -)
  VCC  2│       │7  IN1  (Input 1 +)
 OUT2  3│       │6  IN2  (Input 2 +)
  GND  4│       │5  NF2  (Input 2 -)
        └───────┘
```

| Pin | Connections                                                                                             |
|-----|---------------------------------------------------------------------------------------------------------|
| 1   | Speaker wire A; **and** 4.7 Ω (or 2x 10 ohm parallel) → 100 nF (series Zobel) → pin 3                                            |
| 2   | VRAW (= Arduino 3V3); **and** 100 nF to GND (close to pin, HF bypass); **and** 100 µF to GND (bulk)      |
| 3   | Speaker wire B; **and** 10 µF (`+` at pin 3) to pin 8; **and** 4.7 Ω → 100 nF (series Zobel) → pin 5    |
| 4   | GND rail                                                                                                |
| 5   | 100 nF to GND; (already tied to pin 3 via the Zobel above)                                                |
| 6   | GND                                                                                                     |
| 7   | Volume pot wiper (pin 2); **and** 10 kΩ to GND                                                          |
| 8   | (already tied to pin 3 via the 10 µF feedback cap)                                                      |

This is the same BTL wiring as the final design — OUT1 and OUT2 swing in opposition across the speaker, giving up to ~4× the power into 8 Ω that a single-ended drive would.

Speaker polarity does not matter for a single mono speaker.

## Code

Arduino sketch for the Giga R1 — outputs a 1 kHz sine wave on DAC0 (`A12`).
Pre-computes a 64-sample sine table at start-up, then clocks one sample to
the DAC every ~15.6 µs from `loop()` using `micros()` for timing.

```cpp
// use pins DAC0 (A12) of Arduino Giga R1 and 3.3 V

#include <Arduino.h>
#include <math.h>

constexpr int   DAC_PIN     = A12;     // DAC0 on Giga R1
constexpr float TONE_HZ     = 1000.0f; // test tone frequency
constexpr int   N_SAMPLES   = 64;      // samples per period
constexpr int   MIDPOINT    = 2048;    // 12-bit DAC midpoint (idle level)
constexpr int   AMPLITUDE   = 500;     // peak swing around midpoint

uint16_t sineTable[N_SAMPLES];
uint32_t samplePeriodUs;

void setup() {
    analogWriteResolution(12);

    for (int i = 0; i < N_SAMPLES; i++) {
        float angle = 2.0f * PI * i / N_SAMPLES;
        sineTable[i] = MIDPOINT + (int)(AMPLITUDE * sinf(angle));
    }

    samplePeriodUs = (uint32_t)(1e6f / (TONE_HZ * N_SAMPLES));

    analogWrite(DAC_PIN, MIDPOINT);
    delay(200);
}

void loop() {
    static uint32_t nextUs = 0;
    static int      idx    = 0;

    if ((int32_t)(micros() - nextUs) >= 0) {
        analogWrite(DAC_PIN, sineTable[idx]);
        idx = (idx + 1) % N_SAMPLES;
        nextUs += samplePeriodUs;
    }
}
```

## TODO Next

Add rest of FM Radio circuit. Now at least I know that it is not the speaker's fault that I only get white noise xdd