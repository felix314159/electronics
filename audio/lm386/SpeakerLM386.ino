/*

  COMPONENTS NEEDED:

  Arduino Giga
  Small two wire speaker (I use Ekulit LSM-70A, 0,5 W, 8 Ohm)
  LM386
  1x 220 (or 470) microF cap
  1x 100 microF cap
  1x 10 microF cap
  1x 10 Ohm resistor
  1x 10k Ohm pot
  1x 47 nF cap
  2x 100 nF cap

  ----------------------------------------------------------

  WIRING:

  Arduino Giga +5V    -> breadboard + rail
  Arduino Giga GND    -> breadboard ground rail
  Arduino Giga DAC0   -> row 16 (left half)

  Speaker Plus        -> row 12 (right half)
  Speaker Minus       -> breadboard ground rail

  10k ohm Pot is in rows 20-22 (left half)
  Row 20: 100 nF cap to row 16 (left half), so same row as arduino dac0
  Row 22: breadboard ground rail

  LM386 is in rows 5-8

    Pin 1: 10 microF cap to Pin 8 (+ is at pin 1, - at pin 8)
    Pin 2: breadboard ground rail
    Pin 3: row 21 (pot middle wiper)
    Pin 4: breadboard ground rail
    Pin 5: 220 (or 470) microF cap to row 12 (right half, this is the same row as speaker plus). cap + is at pin 5, cap - is at row 12
    Pin 5: 10 Ohm resistor to row 13, then in same row 13 put 47 nF ceramic cap to breadboard ground rail
    Pin 6: 100 nF cap to row 9 (right half), put it close to the LM
    Pin 6: 100 microF cap to row 9 (right half), put it close to the LM. cap + is at pin 6, cap - goes to row 9. row 9 gets a cable to breadboard ground rail
    Pin 6: Cable to breadboard + rail
    Pin 7: Unused row
    Pin 8: (already connected to pin 1)

*/
#include <math.h>

const int dacPin = A12; //dac0

const float toneHz = 440.0f;       // A4 test tone
const float sampleRate = 20000.0f; // 20 kHz update rate
const int tableSize = 128;

uint16_t sineTable[tableSize];
uint32_t sampleIntervalMicros;

void setup() {
  analogWriteResolution(12);  // use full DAC resolution

  for (int i = 0; i < tableSize; i++) {
    float phase = 2.0f * PI * i / tableSize;
    float s = sinf(phase);

    // Center around midscale because DAC is unipolar
    // 0..4095, centered near 2048
    sineTable[i] = (uint16_t)(2048 + 1800 * s);
  }

  sampleIntervalMicros = (uint32_t)(1000000.0f / sampleRate);
}

void loop() {
  static uint32_t nextMicros = 0;
  static float indexFloat = 0.0f;
  const float step = toneHz * tableSize / sampleRate;

  uint32_t now = micros();
  if ((int32_t)(now - nextMicros) >= 0) {
    nextMicros += sampleIntervalMicros;

    int idx = (int)indexFloat;
    analogWrite(dacPin, sineTable[idx]);

    indexFloat += step;
    if (indexFloat >= tableSize) {
      indexFloat -= tableSize;
    }
  }
}
