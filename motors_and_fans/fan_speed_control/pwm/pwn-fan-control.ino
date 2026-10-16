/*
  ATtiny85 4-wire PWM fan control
  - Supply: 3x AA alkaline, nominally 4.5V and falling during discharge
  - Fan PWM pin: open-collector via BC547; fan power remains connected
  - PWM output: PB1 (physical pin 6) = OC1A
  - Pot input:  PB2 (physical pin 7) = ADC1
  - PWM frequency: 25 kHz (F_CPU=8MHz, prescaler=2, OCR1C=159)

  Arduino IDE:
  - Use ATTinyCore, select ATtiny85, 8 MHz internal, millis()/micros() enabled
  - Set the clock fuses using "Burn Bootloader" with the ISP programmer
  - Timer1 is reserved for this PWM output: do not use tone() or analogWrite()
  - 0% PWM requests stop; whether the fan actually stops is model-dependent
  - Minimum duty and kick duration require testing on the actual fan
*/

#include <Arduino.h>
#include <avr/interrupt.h>

#if !defined(__AVR_ATtiny85__)
#error "This sketch requires an ATtiny85."
#endif
#if F_CPU != 8000000UL
#error "Select 8 MHz internal and set the matching clock fuses."
#endif
#ifdef DISABLEMILLIS
#error "Enable millis()/micros() in ATTinyCore."
#endif

static const uint8_t PWM_PIN = 1;        // Arduino "1" = PB1 (physical pin 6)
static const uint8_t POT_PIN = A1;       // A1 = ADC1 = PB2 (physical pin 7)

// Timer1 TOP for 25 kHz at 8 MHz with prescaler=2:
// f = 8,000,000 / (2 * (TOP+1)) => TOP=159
static const uint8_t TOP = 159;

// Practical behavior tuning:
static const uint8_t MIN_CMD_PERCENT = 20;   // provisional minimum running duty
static const uint16_t KICK_MS = 300;         // startup kick duration
static const uint8_t START_THRESHOLD = 8;    // start at or above this pot percentage
static const uint8_t STOP_THRESHOLD = 5;     // request stop at or below this percentage
static const uint8_t SAMPLE_MS = 10;
static const uint16_t START_ADC = (1023UL * START_THRESHOLD + 99) / 100;
static const uint16_t STOP_ADC = 1023UL * STOP_THRESHOLD / 100;

static_assert(STOP_THRESHOLD < START_THRESHOLD, "Hysteresis thresholds must differ.");
static_assert(START_THRESHOLD <= MIN_CMD_PERCENT && MIN_CMD_PERCENT <= 100,
              "Invalid minimum running duty.");

// Fixed-point ADC filter: eight fractional bits, 1/4 of each new error applied.
static int32_t filteredAdcQ8 = 0;
static uint32_t lastSampleMs = 0;
static uint32_t kickStartedMs = 0;
static bool running = false;
static bool kicking = false;

static void setupPwm25kHz_Timer1_OC1A()
{
  const uint8_t savedSreg = SREG;
  cli();
  TCCR1 = 0;                       // stop Timer1 and disconnect its outputs
  GTCCR = 0;                       // no complementary channel B or dead time
  PLLCSR &= ~(1 << PCKE);          // Timer1 uses CPU clock, not the 64 MHz PLL
  TIMSK &= ~((1 << OCIE1A) | (1 << OCIE1B) | (1 << TOIE1));
  TCNT1 = 0;

  // Preload the port high before enabling the output: BC547 pulls fan PWM low.
  digitalWrite(PWM_PIN, HIGH);
  pinMode(PWM_PIN, OUTPUT);
  OCR1C = TOP;
  OCR1A = TOP;                     // steady high at ATtiny = 0% fan command

  // Timer1 control:
  // - PWM1A = 1 enable PWM on OC1A (PB1)
  // - COM1A1:COM1A0 = 10 => clear OC1A on compare match (non-inverting behavior for OC1A)
  // - CS1[3:0] = 0010 => prescaler 2
  TCCR1 = (1 << PWM1A) | (1 << COM1A1) | (1 << CS11);
  SREG = savedSreg;
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

  // Timer1 explicitly supports steady low at OCR1A=0 and steady high at TOP.
  if (cmdPercent == 0) {
    OCR1A = TOP;
  } else if (cmdPercent == 100) {
    OCR1A = 0;
  } else {
    // Round to the closest timer count; intermediate commands stay in PWM mode.
    uint16_t counts = ((uint16_t)attinyPercent * (TOP + 1) + 50) / 100;
    if (counts >= TOP) counts = TOP - 1;
    OCR1A = (uint8_t)counts;
  }
}

void setup()
{
  setupPwm25kHz_Timer1_OC1A();

  // ADC input pin for pot
  pinMode(POT_PIN, INPUT);

  // One-time settling delay, then seed the filter from the real pot position.
  delay(50);
  filteredAdcQ8 = (int32_t)analogRead(POT_PIN) << 8;
  lastSampleMs = millis() - SAMPLE_MS;
}

void loop()
{
  const uint32_t now = millis();
  if ((uint32_t)(now - lastSampleMs) < SAMPLE_MS) return;
  lastSampleMs = now;

  const uint16_t adc = analogRead(POT_PIN);
  filteredAdcQ8 += (((int32_t)adc << 8) - filteredAdcQ8) / 4;
  const uint16_t filteredAdc = (filteredAdcQ8 + 128) >> 8;

  // The raw stop check lets a turn to minimum cancel a kick at the next sample,
  // without waiting for the filter. Hysteresis prevents immediate restarting.
  if (adc <= STOP_ADC || filteredAdc <= STOP_ADC) {
    running = false;
    kicking = false;
    filteredAdcQ8 = (int32_t)adc << 8;
    setFanCommandPercent(0);
    return;
  }

  if (!running) {
    if (filteredAdc < START_ADC) {
      setFanCommandPercent(0);
      return;
    }
    running = true;
    kicking = true;
    kickStartedMs = now;
  }

  if (kicking && (uint32_t)(now - kickStartedMs) < KICK_MS) {
    setFanCommandPercent(100);
    return;
  }
  kicking = false;

  uint8_t cmdPercent = (uint32_t)filteredAdc * 100 / 1023;
  if (cmdPercent < MIN_CMD_PERCENT) cmdPercent = MIN_CMD_PERCENT;
  setFanCommandPercent(cmdPercent);
}
