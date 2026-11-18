/*
 * User-settable ATtiny85 / HT16K33 clock with Joy-IT COM-KY040RE encoder
 *
 */

#include <Arduino.h>
#include <avr/eeprom.h>
#include <avr/interrupt.h>
#include <avr/power.h>
#include <avr/sleep.h>
#include <avr/wdt.h>
#include <util/atomic.h>
#include <util/delay_basic.h>

#if !defined(__AVR_ATtiny85__)
#error "Select ATtiny85 before compiling this sketch."
#endif

namespace {

constexpr uint8_t kHt16k33Address = 0x70;
constexpr uint8_t kSdaDtBit = PB0;   // physical pin 5: SDA and encoder DT
constexpr uint8_t kButtonBit = PB1;  // physical pin 6: encoder SW
constexpr uint8_t kSclClkBit = PB2;  // physical pin 7: SCL and encoder CLK

constexpr uint8_t kFreshHour = 0;
constexpr uint8_t kFreshMinute = 0;
constexpr uint8_t kFreshSecond = 0;

// C2 deliberately invalidates records made by the former preset-time firmware.
constexpr uint8_t kCheckpointMarker = 0xC2;
constexpr int16_t kClockTrimPpm = 0;

constexpr uint16_t kUiTicksPerSecond = 128;
constexpr uint8_t kButtonDebounceTicks = 2;   // about 16 ms
constexpr uint8_t kBusQuietTicks = 16;        // about 125 ms
constexpr uint16_t kRotationStepTicks = kUiTicksPerSecond;
constexpr uint16_t kLongPressTicks = 2 * kUiTicksPerSecond;

constexpr uint8_t kHourTensMaximum = 2;
constexpr uint8_t kHourOnesMaximum = 9;
constexpr uint8_t kHourOnesMaximumAfterTwo = 3;
constexpr uint8_t kMinuteTensMaximum = 5;
constexpr uint8_t kMinuteOnesMaximum = 9;

constexpr uint8_t hourOnesMaximum(uint8_t hourTens) {
  return hourTens == 2 ? kHourOnesMaximumAfterTwo : kHourOnesMaximum;
}

static_assert(hourOnesMaximum(0) == 9, "Hour ones after 0 must be 0-9");
static_assert(hourOnesMaximum(1) == 9, "Hour ones after 1 must be 0-9");
static_assert(hourOnesMaximum(2) == 3, "Hour ones after 2 must be 0-3");

struct __attribute__((packed)) Checkpoint {
  uint16_t sequence;
  uint8_t hour;
  uint8_t minute;
  uint8_t second;
  uint8_t marker;
  uint8_t reserved;
  uint8_t crc;
};

static_assert(sizeof(Checkpoint) == 8, "Checkpoint must remain eight bytes");
constexpr uint8_t kCheckpointCount = 64;
Checkpoint EEMEM g_eepromCheckpoints[kCheckpointCount];

enum class UiMode : uint8_t {
  kNormal,
  kTimeEdit,
};

volatile uint8_t g_subsecondTicks = 0;
volatile uint8_t g_secondsPending = 0;
volatile uint16_t g_uiTicks = 0;

uint8_t g_hour = kFreshHour;
uint8_t g_minute = kFreshMinute;
uint8_t g_second = kFreshSecond;
uint16_t g_sequence = 0;
int32_t g_trimAccumulator = 0;

UiMode g_uiMode = UiMode::kNormal;
uint8_t g_selectedDigit = 0;
uint8_t g_editHour = 0;
uint8_t g_editMinute = 0;
uint8_t g_editChanged = 0;
uint16_t g_lastRotationActionTick = 0;

volatile uint8_t g_encoderPreviousState = 3;
volatile int8_t g_encoderAccumulator = 0;
volatile int8_t g_encoderPendingDirection = 0;
volatile uint16_t g_lastEncoderSignalTick = 0;
uint8_t g_buttonCandidate = 0;
uint8_t g_buttonStable = 0;
uint8_t g_buttonStableCount = 0;
uint8_t g_buttonLongHandled = 0;
uint16_t g_buttonPressedTick = 0;

bool g_displayInitialized = false;
bool g_displayDirty = true;
uint8_t g_lastBlinkPhase = 0xFF;
uint8_t g_displayShadow[5] = {};

constexpr uint8_t kDigitSegments[10] = {
  0x3F, 0x06, 0x5B, 0x4F, 0x66,
  0x6D, 0x7D, 0x07, 0x7F, 0x6F,
};

// Full quadrature transition table. Clockwise transitions are positive.
constexpr int8_t kEncoderTransition[16] = {
   0,  1, -1,  0,
  -1,  0,  0,  1,
   1,  0,  0, -1,
   0, -1,  1,  0,
};

uint8_t readEncoderState() {
  return static_cast<uint8_t>(((PINB & _BV(kSdaDtBit)) ? 2 : 0) |
                              ((PINB & _BV(kSclClkBit)) ? 1 : 0));
}

uint16_t readUiTicks() {
  uint16_t value;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    value = g_uiTicks;
  }
  return value;
}

uint16_t elapsedTicks(uint16_t now, uint16_t before) {
  return static_cast<uint16_t>(now - before);
}

uint16_t readLastEncoderSignalTick() {
  uint16_t value;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    value = g_lastEncoderSignalTick;
  }
  return value;
}

uint8_t crc8(const uint8_t *bytes, uint8_t length) {
  uint8_t crc = 0;
  while (length--) {
    crc ^= *bytes++;
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc & 0x80) ? static_cast<uint8_t>((crc << 1) ^ 0x07)
                         : static_cast<uint8_t>(crc << 1);
    }
  }
  return crc;
}

constexpr bool validTime(uint8_t hour, uint8_t minute) {
  return hour < 24 && minute < 60;
}

static_assert(validTime(0, 0), "00:00 must be valid");
static_assert(validTime(23, 59), "23:59 must be valid");
static_assert(!validTime(24, 0), "24:00 must be rejected");
static_assert(!validTime(23, 60), "23:60 must be rejected");

bool validCheckpoint(const Checkpoint &record) {
  return record.marker == kCheckpointMarker &&
         validTime(record.hour, record.minute) && record.second < 60 &&
         crc8(reinterpret_cast<const uint8_t *>(&record), 7) == record.crc;
}

bool sequenceIsNewer(uint16_t candidate, uint16_t reference) {
  return static_cast<int16_t>(candidate - reference) > 0;
}

bool restoreNewestCheckpoint() {
  bool found = false;
  Checkpoint newest{};

  for (uint8_t slot = 0; slot < kCheckpointCount; ++slot) {
    Checkpoint candidate;
    eeprom_read_block(&candidate, &g_eepromCheckpoints[slot], sizeof(candidate));
    if (validCheckpoint(candidate) &&
        (!found || sequenceIsNewer(candidate.sequence, newest.sequence))) {
      newest = candidate;
      found = true;
    }
  }

  if (found) {
    g_sequence = newest.sequence;
    g_hour = newest.hour;
    g_minute = newest.minute;
    g_second = newest.second;
  }
  return found;
}

void saveCheckpoint() {
  Checkpoint record{};
  record.sequence = ++g_sequence;
  record.hour = g_hour;
  record.minute = g_minute;
  record.second = g_second;
  record.marker = kCheckpointMarker;
  record.reserved = 0;
  record.crc = crc8(reinterpret_cast<const uint8_t *>(&record), 7);

  Checkpoint *const destination =
      &g_eepromCheckpoints[record.sequence % kCheckpointCount];

  uint8_t oldPayload[7];
  eeprom_read_block(oldPayload, destination, sizeof(oldPayload));
  const uint8_t guaranteedBadCrc = static_cast<uint8_t>(crc8(oldPayload, 7) ^ 0xFF);
  eeprom_update_byte(&destination->crc, guaranteedBadCrc);

  const uint8_t *const source = reinterpret_cast<const uint8_t *>(&record);
  uint8_t *const target = reinterpret_cast<uint8_t *>(destination);
  for (uint8_t offset = 0; offset < 7; ++offset) {
    eeprom_update_byte(target + offset, source[offset]);
  }
  eeprom_update_byte(&destination->crc, record.crc);
}

inline void i2cDelay() {
  _delay_loop_1(2);  // six CPU cycles, about 183 us at 32.768 kHz
}

inline void driveLow(uint8_t bit) {
  DDRB |= _BV(bit);
}

inline void releaseLine(uint8_t bit) {
  DDRB &= static_cast<uint8_t>(~_BV(bit));
}

bool i2cBusIdle() {
  const uint8_t mask = _BV(kSdaDtBit) | _BV(kSclClkBit);
  return (PINB & mask) == mask;
}

void releaseScl() {
  releaseLine(kSclClkBit);
  for (uint8_t timeout = 0;
       timeout != 0xFF && !(PINB & _BV(kSclClkBit)); ++timeout) {
    _delay_loop_1(1);
  }
}

void i2cStart() {
  releaseLine(kSdaDtBit);
  releaseScl();
  i2cDelay();
  driveLow(kSdaDtBit);
  i2cDelay();
  driveLow(kSclClkBit);
}

void i2cStop() {
  driveLow(kSdaDtBit);
  i2cDelay();
  releaseScl();
  i2cDelay();
  releaseLine(kSdaDtBit);
  i2cDelay();
}

bool i2cWriteByte(uint8_t value) {
  for (uint8_t mask = 0x80; mask; mask >>= 1) {
    if (value & mask) {
      releaseLine(kSdaDtBit);
    } else {
      driveLow(kSdaDtBit);
    }
    i2cDelay();
    releaseScl();
    i2cDelay();
    driveLow(kSclClkBit);
  }

  releaseLine(kSdaDtBit);
  i2cDelay();
  releaseScl();
  i2cDelay();
  const bool acknowledged = !(PINB & _BV(kSdaDtBit));
  driveLow(kSclClkBit);
  return acknowledged;
}

bool htCommand(uint8_t command) {
  if (!i2cBusIdle()) {
    return false;
  }
  i2cStart();
  const bool ok = i2cWriteByte(static_cast<uint8_t>(kHt16k33Address << 1)) &&
                  i2cWriteByte(command);
  i2cStop();
  return ok;
}

void buildDisplayWords(uint16_t now, uint8_t words[5]) {
  const bool editing = g_uiMode == UiMode::kTimeEdit;
  const uint8_t displayHour = editing ? g_editHour : g_hour;
  const uint8_t displayMinute = editing ? g_editMinute : g_minute;
  const bool showLeadingZero = g_uiMode != UiMode::kNormal;
  const bool blinkOn = ((now / 64U) & 1U) != 0;

  words[0] = static_cast<uint8_t>((showLeadingZero || displayHour >= 10)
                                      ? kDigitSegments[displayHour / 10]
                                      : 0);
  words[1] = kDigitSegments[displayHour % 10];
  words[2] = 0x02;
  words[3] = kDigitSegments[displayMinute / 10];
  words[4] = kDigitSegments[displayMinute % 10];

  if (g_uiMode != UiMode::kNormal && !blinkOn) {
    const uint8_t wordIndex =
        g_selectedDigit < 2 ? g_selectedDigit : g_selectedDigit + 1;
    words[wordIndex] = 0;
  }
}

bool htWriteFullDisplay(const uint8_t words[5]) {
  if (!i2cBusIdle()) {
    return false;
  }
  i2cStart();
  bool ok = i2cWriteByte(static_cast<uint8_t>(kHt16k33Address << 1));
  ok = i2cWriteByte(0x00) && ok;
  for (uint8_t index = 0; index < 5; ++index) {
    ok = i2cWriteByte(words[index]) && ok;
    ok = i2cWriteByte(0x00) && ok;
  }
  i2cStop();
  return ok;
}

bool htWriteDisplayWord(uint8_t wordIndex, uint8_t value) {
  if (!i2cBusIdle()) {
    return false;
  }
  i2cStart();
  bool ok = i2cWriteByte(static_cast<uint8_t>(kHt16k33Address << 1));
  ok = i2cWriteByte(static_cast<uint8_t>(wordIndex * 2)) && ok;
  ok = i2cWriteByte(value) && ok;
  ok = i2cWriteByte(0x00) && ok;
  i2cStop();
  return ok;
}

void pauseEncoderCapture() {
  GIMSK &= static_cast<uint8_t>(~_BV(PCIE));
}

void synchronizeEncoderAfterI2c(uint16_t now) {
  g_encoderPreviousState = readEncoderState();
  g_encoderAccumulator = 0;
  g_lastEncoderSignalTick = now;
  GIFR = _BV(PCIF);
  GIMSK |= _BV(PCIE);
}

void serviceDisplay(uint16_t now) {
  const uint8_t blinkPhase = static_cast<uint8_t>((now / 64U) & 1U);
  if (g_uiMode != UiMode::kNormal && blinkPhase != g_lastBlinkPhase) {
    g_lastBlinkPhase = blinkPhase;
    g_displayDirty = true;
  }

  if (g_displayInitialized && !g_displayDirty) {
    return;
  }
  if (elapsedTicks(now, readLastEncoderSignalTick()) < kBusQuietTicks ||
      !i2cBusIdle()) {
    return;
  }

  uint8_t targetWords[5];
  buildDisplayWords(now, targetWords);

  if (!g_displayInitialized) {
    pauseEncoderCapture();
    const bool ok = htCommand(0x21) &&  // oscillator on
                    htCommand(0x81) &&  // display on, blink off
                    htCommand(0xE0) &&  // minimum brightness
                    htWriteFullDisplay(targetWords);
    synchronizeEncoderAfterI2c(now);
    g_displayInitialized = ok;
    g_displayDirty = !ok;
    if (ok) {
      for (uint8_t index = 0; index < 5; ++index) {
        g_displayShadow[index] = targetWords[index];
      }
    }
    return;
  }

  uint8_t changedWord = 5;
  for (uint8_t index = 0; index < 5; ++index) {
    if (g_displayShadow[index] != targetWords[index]) {
      changedWord = index;
      break;
    }
  }
  if (changedWord == 5) {
    g_displayDirty = false;
    return;
  }

  pauseEncoderCapture();
  const bool ok = htWriteDisplayWord(changedWord, targetWords[changedWord]);
  synchronizeEncoderAfterI2c(now);
  if (!ok) {
    g_displayDirty = true;
    return;
  }

  g_displayShadow[changedWord] = targetWords[changedWord];
  g_displayDirty = false;
  for (uint8_t index = 0; index < 5; ++index) {
    if (g_displayShadow[index] != targetWords[index]) {
      g_displayDirty = true;
      break;
    }
  }
}

void advanceOneSecond() {
  if (++g_second == 60) {
    g_second = 0;
    if (++g_minute == 60) {
      g_minute = 0;
      if (++g_hour == 24) {
        g_hour = 0;
      }
    }
    if (g_uiMode != UiMode::kTimeEdit) {
      g_displayDirty = true;
    }
    if ((g_minute & 1U) == 0) {
      saveCheckpoint();
    }
  }
}

void processCrystalSecond() {
  g_trimAccumulator += kClockTrimPpm;
  if (g_trimAccumulator <= -1000000L) {
    g_trimAccumulator += 1000000L;
    return;
  }
  advanceOneSecond();
  if (g_trimAccumulator >= 1000000L) {
    g_trimAccumulator -= 1000000L;
    advanceOneSecond();
  }
}

uint8_t wrapValue(uint8_t value, int8_t direction, uint8_t maximum) {
  if (direction > 0) {
    return value == maximum ? 0 : static_cast<uint8_t>(value + 1);
  }
  return value == 0 ? maximum : static_cast<uint8_t>(value - 1);
}

bool adjustEditedDigit(int8_t direction) {
  const uint8_t oldHour = g_editHour;
  const uint8_t oldMinute = g_editMinute;
  uint8_t hourTens = g_editHour / 10;
  uint8_t hourOnes = g_editHour % 10;
  uint8_t minuteTens = g_editMinute / 10;
  uint8_t minuteOnes = g_editMinute % 10;

  switch (g_selectedDigit) {
    case 0: {
      const uint8_t candidate =
          wrapValue(hourTens, direction, kHourTensMaximum);
      // Changing digit 1 must never modify digit 2. Reject 24-29 instead.
      if (candidate != 2 || hourOnes <= kHourOnesMaximumAfterTwo) {
        hourTens = candidate;
      }
      break;
    }
    case 1:
      hourOnes = wrapValue(hourOnes, direction,
                           hourOnesMaximum(hourTens));
      break;
    case 2:
      minuteTens = wrapValue(minuteTens, direction, kMinuteTensMaximum);
      break;
    default:
      minuteOnes = wrapValue(minuteOnes, direction, kMinuteOnesMaximum);
      break;
  }

  g_editHour = static_cast<uint8_t>(hourTens * 10 + hourOnes);
  g_editMinute = static_cast<uint8_t>(minuteTens * 10 + minuteOnes);
  return g_editHour != oldHour || g_editMinute != oldMinute;
}

void enterTimeEdit(uint16_t now) {
  g_editHour = g_hour;
  g_editMinute = g_minute;
  g_editChanged = 0;
  g_selectedDigit = 0;
  g_uiMode = UiMode::kTimeEdit;
  // Permit one immediate encoder action after entering settings.
  g_lastRotationActionTick =
      static_cast<uint16_t>(now - kRotationStepTicks);
  g_displayDirty = true;
}

void handleRotation(int8_t direction, uint16_t now) {
  if (g_uiMode == UiMode::kNormal ||
      elapsedTicks(now, g_lastRotationActionTick) < kRotationStepTicks) {
    return;
  }

  // Extra detents during the one-second interval are deliberately discarded.
  g_lastRotationActionTick = now;
  if (adjustEditedDigit(direction)) {
    g_editChanged = 1;
    g_displayDirty = true;
  }
}

void handleShortButtonPress() {
  if (g_uiMode == UiMode::kNormal) {
    return;
  }

  g_selectedDigit = static_cast<uint8_t>((g_selectedDigit + 1) & 0x03);
  g_displayDirty = true;
}

void finishTimeEdit() {
  // Never persist an invalid time, even if future edit logic regresses.
  if (g_editChanged && validTime(g_editHour, g_editMinute)) {
    g_hour = g_editHour;
    g_minute = g_editMinute;
    g_second = 0;
    saveCheckpoint();
  }
  g_uiMode = UiMode::kNormal;
  g_displayDirty = true;
}

void handleLongButtonPress(uint16_t now) {
  if (g_uiMode == UiMode::kNormal) {
    enterTimeEdit(now);
  } else {
    finishTimeEdit();
  }
}

void pollEncoder(uint16_t now) {
  int8_t direction;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    direction = g_encoderPendingDirection;
    g_encoderPendingDirection = 0;
  }
  if (direction != 0) {
    handleRotation(direction, now);
  }
}

void pollButton(uint16_t now) {
  const uint8_t pressed = (PINB & _BV(kButtonBit)) ? 0 : 1;
  if (pressed != g_buttonCandidate) {
    g_buttonCandidate = pressed;
    g_buttonStableCount = 0;
    return;
  }
  if (g_buttonStableCount < kButtonDebounceTicks) {
    ++g_buttonStableCount;
    return;
  }
  if (g_buttonStable != g_buttonCandidate) {
    g_buttonStable = g_buttonCandidate;
    if (g_buttonStable) {
      g_buttonPressedTick = now;
      g_buttonLongHandled = 0;
    } else if (!g_buttonLongHandled) {
      handleShortButtonPress();
    }
  }

  if (g_buttonStable && !g_buttonLongHandled &&
      elapsedTicks(now, g_buttonPressedTick) >= kLongPressTicks) {
    g_buttonLongHandled = 1;
    handleLongButtonPress(now);
  }
}

void startOneSecondTimebase() {
  // CTC at 32,768 / (1 * (255 + 1)) = 128 interrupts per second.
  TCCR0A = _BV(WGM01);
  TCCR0B = _BV(CS00);
  TCNT0 = 0;
  OCR0A = 255;
  TIFR = _BV(OCF0A);
  TIMSK = _BV(OCIE0A);
}

}  // namespace

ISR(PCINT0_vect) {
  const uint8_t currentState = readEncoderState();
  const uint8_t previousState = g_encoderPreviousState;
  if (currentState == previousState) {
    return;
  }

  g_encoderPreviousState = currentState;
  g_lastEncoderSignalTick = g_uiTicks;
  const int8_t delta =
      kEncoderTransition[(previousState << 2) | currentState];
  if (delta == 0) {
    // Both phases changed before they could be sampled, restart at this state.
    g_encoderAccumulator = 0;
    return;
  }

  int8_t accumulator = static_cast<int8_t>(g_encoderAccumulator + delta);
  if (accumulator > 4) {
    accumulator = 4;
  } else if (accumulator < -4) {
    accumulator = -4;
  }
  g_encoderAccumulator = accumulator;

  // KY-040 variants can detent at 00, 11, or both. Two consistent phase
  // transitions therefore represent one physical movement. Only one event is
  // retained, fast turns cannot build a queue of future digit changes.
  if (currentState == 0 || currentState == 3) {
    if (g_encoderPendingDirection == 0) {
      if (accumulator >= 2) {
        g_encoderPendingDirection = 1;
      } else if (accumulator <= -2) {
        g_encoderPendingDirection = -1;
      }
    }
    g_encoderAccumulator = 0;
  }
}

ISR(TIMER0_COMPA_vect) {
  ++g_uiTicks;
  if (++g_subsecondTicks == kUiTicksPerSecond) {
    g_subsecondTicks = 0;
    if (g_secondsPending != 0xFF) {
      ++g_secondsPending;
    }
  }
}

void setup() {
  MCUSR = 0;
  wdt_disable();

  // SDA/DT and SCL/CLK remain open-drain. SW is an input with pull-up.
  PORTB &= static_cast<uint8_t>(~(_BV(kSdaDtBit) | _BV(kSclClkBit)));
  releaseLine(kSdaDtBit);
  releaseLine(kSclClkBit);
  DDRB &= static_cast<uint8_t>(~_BV(kButtonBit));
  PORTB |= _BV(kButtonBit);

  ADCSRA &= static_cast<uint8_t>(~_BV(ADEN));
  ACSR |= _BV(ACD);
  power_adc_disable();
  power_usi_disable();
  power_timer1_disable();

  restoreNewestCheckpoint();
  g_encoderPreviousState = readEncoderState();
  g_encoderAccumulator = 0;
  g_encoderPendingDirection = 0;
  PCMSK = _BV(PCINT0) | _BV(PCINT2);
  GIFR = _BV(PCIF);
  GIMSK |= _BV(PCIE);
  g_buttonCandidate = (PINB & _BV(kButtonBit)) ? 0 : 1;
  g_buttonStable = g_buttonCandidate;

  startOneSecondTimebase();
  sei();
  set_sleep_mode(SLEEP_MODE_IDLE);
}

void loop() {
  uint8_t pending;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    pending = g_secondsPending;
    g_secondsPending = 0;
  }
  if (g_uiMode == UiMode::kNormal) {
    while (pending--) {
      processCrystalSecond();
    }
  }

  const uint16_t now = readUiTicks();
  pollEncoder(now);
  pollButton(now);
  serviceDisplay(now);
  sleep_mode();
}
