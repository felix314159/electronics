/*
  ATtiny85 + KY-040 + 74HC595
  One of four LEDs is always active.
  Rotate encoder to move selection.

  Wiring:
    PB3 (physical pin 2) <- encoder CLK
    PB4 (physical pin 3) <- encoder DT

    PB0 (physical pin 5) -> 74HC595 SER   (pin 14)
    PB1 (physical pin 6) -> 74HC595 SRCLK (pin 11)
    PB2 (physical pin 7) -> 74HC595 RCLK  (pin 12)

  74HC595 outputs:
    QA (pin 15) -> Green
    QB (pin 1)  -> Yellow
    QC (pin 2)  -> Red
    QD (pin 3)  -> Blue
*/

const uint8_t ENC_CLK   = 3;  // PB3
const uint8_t ENC_DT    = 4;  // PB4

const uint8_t SR_SER    = 0;  // PB0
const uint8_t SR_SRCLK  = 1;  // PB1
const uint8_t SR_RCLK   = 2;  // PB2

// LED bit positions in 74HC595 output byte
// QA = bit 0, QB = bit 1, QC = bit 2, QD = bit 3
const uint8_t ledPatterns[4] = {
  0b00000001, // Green  -> QA
  0b00000010, // Yellow -> QB
  0b00000100, // Red    -> QC
  0b00001000  // Blue   -> QD
};

volatile int8_t selectedLed = 0;

// Encoder state tracking
uint8_t lastCLK = HIGH;
unsigned long lastMoveMs = 0;
const unsigned long debounceMs = 3;   // helps with KY-040 bounce

void shiftRegisterWrite(uint8_t value) {
  digitalWrite(SR_RCLK, LOW);
  shiftOut(SR_SER, SR_SRCLK, MSBFIRST, value);
  digitalWrite(SR_RCLK, HIGH);
}

void updateLED() {
  shiftRegisterWrite(ledPatterns[selectedLed]);
}

void setup() {
  pinMode(ENC_CLK, INPUT_PULLUP);
  pinMode(ENC_DT, INPUT_PULLUP);

  pinMode(SR_SER, OUTPUT);
  pinMode(SR_SRCLK, OUTPUT);
  pinMode(SR_RCLK, OUTPUT);

  digitalWrite(SR_SER, LOW);
  digitalWrite(SR_SRCLK, LOW);
  digitalWrite(SR_RCLK, LOW);

  lastCLK = digitalRead(ENC_CLK);

  updateLED();
}

void loop() {
  uint8_t currentCLK = digitalRead(ENC_CLK);

  // Detect falling edge on CLK
  if (lastCLK == HIGH && currentCLK == LOW) {
    unsigned long now = millis();

    if (now - lastMoveMs >= debounceMs) {
      uint8_t currentDT = digitalRead(ENC_DT);

      // Direction:
      // If DT != CLK on the edge, one direction; else the other.
      if (currentDT != currentCLK) {
        selectedLed++;
      } else {
        selectedLed--;
      }

      if (selectedLed > 3) selectedLed = 0;
      if (selectedLed < 0) selectedLed = 3;

      updateLED();
      lastMoveMs = now;
    }
  }

  lastCLK = currentCLK;
}
