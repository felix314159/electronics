/*
  ATtiny85 4-wire PWM fan control (proper PWM pin control)
  - Fan supply: constant 5V
  - Fan PWM pin: open-collector via NPN transistor
  - PWM output: PB1 (physical pin 6) = OC1A
  - Pot input:  PB2 (physical pin 7) = ADC1
  - PWM frequency: 25 kHz (F_CPU=8MHz, prescaler=2, OCR1C=159)

  Arduino IDE:
  - Use an ATtiny core (e.g., ATTinyCore)
  - Select ATtiny85, 8 MHz internal
*/

#include <Arduino.h>

static const uint8_t PWM_PIN = 1;        // Arduino "1" = PB1 (physical pin 6)
static const uint8_t POT_PIN = A1;       // A1 = ADC1 = PB2 (physical pin 7)

// Timer1 TOP for 25 kHz at 8 MHz with prescaler=2:
// f = 8,000,000 / (2 * (TOP+1)) => TOP=159
static const uint8_t TOP = 159;

// Practical behavior tuning:
static const uint8_t MIN_CMD_PERCENT = 20;   // clamp low end while running (avoid stall)
static const uint16_t KICK_MS = 300;         // startup kick duration
static const uint8_t START_THRESHOLD = 8;    // below this % we consider "off"

static uint8_t lastCmdPercent = 0;

static void setupPwm25kHz_Timer1_OC1A()
{
  pinMode(PWM_PIN, OUTPUT);

  // Set TOP
  OCR1C = TOP;

  // Start with "off" command (fan PWM pin held low most of the time)
  OCR1A = TOP;  // ATtiny output ~100% high -> transistor pulls fan PWM low ~100%

  // Timer1 control:
  // - PWM1A = 1 enable PWM on OC1A (PB1)
  // - COM1A1:COM1A0 = 10 => clear OC1A on compare match (non-inverting behavior for OC1A)
  // - CS1[3:0] = 0010 => prescaler 2
  TCCR1 = (1 << PWM1A) | (1 << COM1A1) | (1 << CS11);

  // No OC1B needed
  GTCCR = 0;
}

static void setFanCommandPercent(uint8_t cmdPercent)
{
  // cmdPercent = 0..100 means "fan PWM pin high" duty 0..100 (faster with higher)
  if (cmdPercent > 100) cmdPercent = 100;

  // Inversion compensation for open-collector NPN:
  // We want FAN_PWM_HIGH_DUTY = cmdPercent.
  // Our ATtiny OC1A drives the NPN base; OC1A high => fan PWM LOW.
  // Therefore: ATtiny_high_duty = 100 - cmdPercent.
  uint8_t attinyPercent = 100 - cmdPercent;

  // Convert percent to OCR1A (0..TOP)
  // duty ~= (OCR1A+1)/(TOP+1) => OCR1A ~= duty*(TOP+1)-1
  uint16_t tmp = (uint16_t)attinyPercent * (TOP + 1);
  uint8_t ocr = 0;
  if (tmp == 0) {
    ocr = 0;
  } else {
    ocr = (uint8_t)((tmp / 100) - 1);
    if (ocr > TOP) ocr = TOP;
  }

  OCR1A = ocr;
}

void setup()
{
  setupPwm25kHz_Timer1_OC1A();

  // ADC input pin for pot
  pinMode(POT_PIN, INPUT);

  // Optional: small delay for supply settling
  delay(50);
}

void loop()
{
  int adc = analogRead(POT_PIN);                 // 0..1023
  uint8_t cmdPercent = (uint32_t)adc * 100 / 1023;

  // Treat very low pot as "off-ish"
  if (cmdPercent < START_THRESHOLD) {
    cmdPercent = 0;
  } else {
    // Clamp to a minimum running command to reduce stalls at low RPM
    if (cmdPercent < MIN_CMD_PERCENT) cmdPercent = MIN_CMD_PERCENT;
  }

  // Kick-start when moving from 0 to nonzero
  if (lastCmdPercent == 0 && cmdPercent > 0) {
    setFanCommandPercent(100);
    delay(KICK_MS);
  }

  setFanCommandPercent(cmdPercent);
  lastCmdPercent = cmdPercent;

  delay(20); // basic smoothing / reduces jitter
}

