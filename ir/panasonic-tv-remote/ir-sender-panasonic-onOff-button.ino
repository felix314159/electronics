// attiny85

#include <IRremote.hpp> // install 'IRremote' library

// ATtiny85 sender
#define IR_SEND_PIN 1                 // PB1 (physical pin 6)
#define SEND_PWM_BY_TIMER             // important on slow AVRs :contentReference[oaicite:3]{index=3}
#define NO_LED_SEND_FEEDBACK_CODE     // avoid any feedback LED pin shenanigans

// ATtiny85 has two pwm-capable pins: pin 5 (pb0) and pin 6 (pb1)
int IR_SENDER_TSAL6200 = 1; // pb1 (pin 6, MISO)
const bool     LED_FEEDBACK = false;
const uint8_t  FEEDBACK_LED_PIN = 0;

int PUSH_BUTTON = 2; // PB2 (pin 7)

void setup() {
  pinMode(PUSH_BUTTON, INPUT_PULLUP); // wired from attiny GND (pin 4) to pin 7

  IrSender.begin(IR_SENDER_TSAL6200, LED_FEEDBACK, FEEDBACK_LED_PIN);
}

void loop() {
  if (digitalRead(PB2) == LOW) {
        IrSender.sendPanasonic(0x8, 0x3D, 0); //3d is on/off button, u can also set 0 to 2 (original remote sends 2 additional repeats)
    }
  
  delay(100);
}
// Note: i have to be pretty close to the TV for it to work, maybe i should have added transistor driver stage for better range.