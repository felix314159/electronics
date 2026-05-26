/*
  ATtiny85 + INA219 + 4 digit display current meter

  Physical wiring, ATtiny85 DIP:
    pin 1: RESET, unused
    pin 2: PB3 / Arduino D3 -> display CLK, yellow wire
    pin 3: PB4 / Arduino D4 -> display DATA/DIO, white wire
    pin 4: GND              -> shared ground rail
    pin 5: PB0 / SDA        -> INA219 SDA, white wire
    pin 6: PB1 / Arduino D1 -> unused
    pin 7: PB2 / SCL        -> INA219 SCL, yellow wire
    pin 8: VCC              -> battery-side +5 V rail

  INA219 calibration below uses the common Adafruit breakout shunt:
    0.1 ohm shunt, 32 V bus range, +/-3.2 A shunt range,
    0.1 mA internal current LSB.

  The display is driven as a TM1650-style 2-wire display. This matches the
  Crowtail 4 digit display code used in the lesson16 display project.
*/

#include <Arduino.h>

#include <Wire.h>

#define I2C Wire
static uint8_t i2cReadByte() { return I2C.read(); }
static void i2cWriteByte(uint8_t value) { I2C.write(value); }

const uint8_t INA219_ADDR = 0x40;

const uint8_t DISPLAY_CLK_PIN = 3;  // ATtiny85 physical pin 2, PB3
const uint8_t DISPLAY_DATA_PIN = 4; // ATtiny85 physical pin 3, PB4

const uint8_t TM1650_MODE_CMD = 0x48;
const uint8_t TM1650_BRIGHT_DARKEST = 0x11;
const uint8_t TM1650_DIGIT_ADDR[4] = { 0x68, 0x6A, 0x6C, 0x6E };

const uint16_t INA219_REG_CONFIG = 0x00;
const uint16_t INA219_REG_SHUNT_VOLTAGE = 0x01;
const uint16_t INA219_REG_BUS_VOLTAGE = 0x02;
const uint16_t INA219_REG_CURRENT = 0x04;
const uint16_t INA219_REG_CALIBRATION = 0x05;

const uint16_t INA219_CONFIG_32V_320MV_12BIT_CONTINUOUS = 0x399F;
const uint16_t INA219_CALIBRATION_0R1_100UA = 4096;

const uint8_t SEG_BLANK = 0x00;
const uint8_t SEG_MINUS = 0x40;

const uint8_t DIGIT_TO_SEGMENT[10] = {
  0x3F, // 0
  0x06, // 1
  0x5B, // 2
  0x4F, // 3
  0x66, // 4
  0x6D, // 5
  0x7D, // 6
  0x07, // 7
  0x7F, // 8
  0x6F  // 9
};

void displayDelay()
{
  delayMicroseconds(5);
}

void displayStart()
{
  pinMode(DISPLAY_DATA_PIN, OUTPUT);
  digitalWrite(DISPLAY_CLK_PIN, HIGH);
  digitalWrite(DISPLAY_DATA_PIN, HIGH);
  displayDelay();
  digitalWrite(DISPLAY_DATA_PIN, LOW);
  displayDelay();
  digitalWrite(DISPLAY_CLK_PIN, LOW);
}

void displayStop()
{
  pinMode(DISPLAY_DATA_PIN, OUTPUT);
  digitalWrite(DISPLAY_CLK_PIN, LOW);
  digitalWrite(DISPLAY_DATA_PIN, LOW);
  displayDelay();
  digitalWrite(DISPLAY_CLK_PIN, HIGH);
  displayDelay();
  digitalWrite(DISPLAY_DATA_PIN, HIGH);
  displayDelay();
}

bool displayWriteByte(uint8_t value)
{
  pinMode(DISPLAY_DATA_PIN, OUTPUT);
  for (uint8_t bit = 0; bit < 8; bit++) {
    digitalWrite(DISPLAY_CLK_PIN, LOW);
    digitalWrite(DISPLAY_DATA_PIN, (value & 0x80) ? HIGH : LOW);
    displayDelay();
    digitalWrite(DISPLAY_CLK_PIN, HIGH);
    displayDelay();
    value <<= 1;
  }

  digitalWrite(DISPLAY_CLK_PIN, LOW);
  pinMode(DISPLAY_DATA_PIN, INPUT_PULLUP);
  displayDelay();

  uint8_t timeout = 200;
  while (digitalRead(DISPLAY_DATA_PIN) == HIGH && timeout > 0) {
    timeout--;
    delayMicroseconds(2);
  }

  bool ack = timeout > 0;
  digitalWrite(DISPLAY_CLK_PIN, HIGH);
  displayDelay();
  digitalWrite(DISPLAY_CLK_PIN, LOW);
  pinMode(DISPLAY_DATA_PIN, OUTPUT);
  return ack;
}

void displayRaw(const uint8_t segments[4])
{
  displayStart();
  displayWriteByte(TM1650_MODE_CMD);
  displayWriteByte(TM1650_BRIGHT_DARKEST);
  displayStop();

  for (uint8_t i = 0; i < 4; i++) {
    displayStart();
    displayWriteByte(TM1650_DIGIT_ADDR[i]);
    displayWriteByte(segments[i]);
    displayStop();
  }
}

void displayNumber(int value)
{
  uint8_t segments[4] = { SEG_BLANK, SEG_BLANK, SEG_BLANK, SEG_BLANK };

  if (value > 9999) {
    segments[0] = DIGIT_TO_SEGMENT[9];
    segments[1] = DIGIT_TO_SEGMENT[9];
    segments[2] = DIGIT_TO_SEGMENT[9];
    segments[3] = DIGIT_TO_SEGMENT[9];
    displayRaw(segments);
    return;
  }

  if (value < -999) {
    segments[0] = SEG_MINUS;
    segments[1] = DIGIT_TO_SEGMENT[9];
    segments[2] = DIGIT_TO_SEGMENT[9];
    segments[3] = DIGIT_TO_SEGMENT[9];
    displayRaw(segments);
    return;
  }

  bool negative = value < 0;
  uint16_t magnitude = negative ? -value : value;

  for (int8_t pos = 3; pos >= 0; pos--) {
    segments[pos] = DIGIT_TO_SEGMENT[magnitude % 10];
    magnitude /= 10;
    if (magnitude == 0) {
      if (negative && pos > 0) {
        segments[pos - 1] = SEG_MINUS;
      }
      break;
    }
  }

  displayRaw(segments);
}

void displayError()
{
  uint8_t segments[4] = {
    0x79, // E
    0x50, // r
    0x50, // r
    SEG_BLANK
  };
  displayRaw(segments);
}

void inaWrite16(uint8_t reg, uint16_t value)
{
  I2C.beginTransmission(INA219_ADDR);
  i2cWriteByte(reg);
  i2cWriteByte(value >> 8);
  i2cWriteByte(value & 0xFF);
  I2C.endTransmission();
}

bool inaRead16(uint8_t reg, uint16_t *value)
{
  I2C.beginTransmission(INA219_ADDR);
  i2cWriteByte(reg);
  if (I2C.endTransmission() != 0) {
    return false;
  }

  if (I2C.requestFrom(INA219_ADDR, (uint8_t)2) != 2) {
    return false;
  }

  uint8_t highByte = i2cReadByte();
  uint8_t lowByte = i2cReadByte();
  *value = ((uint16_t)highByte << 8) | lowByte;
  return true;
}

void ina219Begin()
{
  inaWrite16(INA219_REG_CALIBRATION, INA219_CALIBRATION_0R1_100UA);
  inaWrite16(INA219_REG_CONFIG, INA219_CONFIG_32V_320MV_12BIT_CONTINUOUS);
}

bool readCurrent_mA(int *current_mA)
{
  uint16_t rawCurrent;

  // Adafruit's driver does this too: sharp loads can reset the INA219 and
  // clear calibration, which makes the current register invalid.
  inaWrite16(INA219_REG_CALIBRATION, INA219_CALIBRATION_0R1_100UA);

  if (!inaRead16(INA219_REG_CURRENT, &rawCurrent)) {
    return false;
  }

  int16_t signedCurrent = (int16_t)rawCurrent;

  // Calibration above makes current register LSB = 0.1 mA.
  if (signedCurrent >= 0) {
    *current_mA = (signedCurrent + 5) / 10;
  } else {
    *current_mA = -((-signedCurrent + 5) / 10);
  }
  return true;
}

bool readLoadVoltage_mV(uint16_t *loadVoltage_mV)
{
  uint16_t rawBusVoltage;
  uint16_t rawShuntVoltage;

  if (!inaRead16(INA219_REG_BUS_VOLTAGE, &rawBusVoltage)) {
    return false;
  }
  if (!inaRead16(INA219_REG_SHUNT_VOLTAGE, &rawShuntVoltage)) {
    return false;
  }

  uint16_t busVoltage_mV = (rawBusVoltage >> 3) * 4;
  int16_t shuntVoltage_10uV = (int16_t)rawShuntVoltage;
  int16_t shuntVoltage_mV = shuntVoltage_10uV / 100;

  *loadVoltage_mV = busVoltage_mV + shuntVoltage_mV;
  return true;
}

void setup()
{
  pinMode(DISPLAY_CLK_PIN, OUTPUT);
  pinMode(DISPLAY_DATA_PIN, OUTPUT);
  digitalWrite(DISPLAY_CLK_PIN, HIGH);
  digitalWrite(DISPLAY_DATA_PIN, HIGH);
  displayNumber(0);

  I2C.begin();
  delay(50);
  ina219Begin();
}

void loop()
{
  static uint8_t loopCount = 0;

  int current_mA;
  if (!readCurrent_mA(&current_mA)) {
    displayError();
    delay(500);
    ina219Begin();
    return;
  }

  displayNumber(current_mA);

  // Re-write calibration occasionally; INA219 can lose it after reset/brownout.
  loopCount++;
  if (loopCount == 0) {
    ina219Begin();
  }

  delay(250);
}
