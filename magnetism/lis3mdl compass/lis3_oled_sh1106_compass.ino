// 'Adafruit SH110X' has to be installed for the OLED
// 'Adafruit LIS3MDL' has to be installed for the compass (https://www.adafruit.com/product/4479)

// Wiring (using Arduino Giga):

// i use I2C4_SCL + I2C4_SDA shared between oled and lis3mdl
// 3.3 V for everything

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SH110X.h>
#include <Adafruit_LIS3MDL.h>

// Giga R1 I2C buses:
//   Wire  -> SDA (D20)  / SCL (D21)
//   Wire1 -> SDA1 (D102) / SCL1 (D101)   <- dedicated "I2C4" header
//   Wire2 -> SDA2 (D9)   / SCL2 (D8)     (no internal pullups)
#define I2C_BUS Wire1

Adafruit_SH1106G display = Adafruit_SH1106G(128, 64, &I2C_BUS, -1);
Adafruit_LIS3MDL lis3mdl;

const int CX = 64;   // compass center x
const int CY = 32;   // compass center y
const int R  = 24;   // compass outer radius

void setup() {
  Serial.begin(115200);
  unsigned long start = millis();
  while (!Serial && millis() - start < 5000) delay(10);

  I2C_BUS.begin();
  delay(100);

  if (!display.begin(0x3C, true)) {
    Serial.println("SH1106 init failed");
    while (1) delay(1);
  }

  // Adafruit breakout default is 0x1C, alt 0x1E (if SDO/SA1 is pulled high)
  if (!lis3mdl.begin_I2C(0x1C, &I2C_BUS) && !lis3mdl.begin_I2C(0x1E, &I2C_BUS)) {
    Serial.println("LIS3MDL not found");
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SH110X_WHITE);
    display.setCursor(0, 0);
    display.println("LIS3MDL not found");
    display.display();
    while (1) delay(1);
  }

  lis3mdl.setPerformanceMode(LIS3MDL_MEDIUMMODE);
  lis3mdl.setOperationMode(LIS3MDL_CONTINUOUSMODE);
  lis3mdl.setDataRate(LIS3MDL_DATARATE_155_HZ);
  lis3mdl.setRange(LIS3MDL_RANGE_4_GAUSS);
}

// degrees measured clockwise from "up" on the screen
static void polar(float deg, int r, int &x, int &y) {
  float rad = deg * PI / 180.0f;
  x = CX + (int)roundf(r * sinf(rad));
  y = CY - (int)roundf(r * cosf(rad));
}

static float readHeadingDeg() {
  sensors_event_t ev;
  lis3mdl.getEvent(&ev);
  // +180 compensates for the sensor orientation on the breakout relative to the board
  float h = atan2f(ev.magnetic.y, ev.magnetic.x) * 180.0f / PI + 180.0f;
  if (h >= 360.0f) h -= 360.0f;
  if (h < 0)      h += 360.0f;
  return h;
}

static void drawCompass(float heading) {
  display.clearDisplay();

  // Outer circle
  display.drawCircle(CX, CY, R, SH110X_WHITE);

  // Tick marks every 30 deg, longer at the cardinals; rotate with the rose
  for (int a = 0; a < 360; a += 30) {
    int x1, y1, x2, y2;
    int inner = R - ((a % 90 == 0) ? 5 : 3);
    polar(a - heading, R,     x1, y1);
    polar(a - heading, inner, x2, y2);
    display.drawLine(x1, y1, x2, y2, SH110X_WHITE);
  }

  // Cardinal labels rotate with the rose so N stays at magnetic north
  display.setTextSize(1);
  display.setTextColor(SH110X_WHITE);
  const char *labels[] = {"N", "E", "S", "W"};
  const int  angles[] = {  0,  90, 180, 270};
  for (int i = 0; i < 4; i++) {
    int lx, ly;
    polar(angles[i] - heading, R + 4, lx, ly);
    display.setCursor(lx - 2, ly - 3);
    display.print(labels[i]);
  }

  // Arrow pointing to magnetic north: wide arrowhead + thick shaft
  const float rad = -heading * PI / 180.0f;
  const float nx = sinf(rad),  ny = -cosf(rad);   // unit vector toward N on screen
  const float px = -ny,        py =  nx;          // perpendicular (right of the arrow)

  auto pt = [&](float along, float perp, int &x, int &y) {
    x = CX + (int)roundf(nx * along + px * perp);
    y = CY + (int)roundf(ny * along + py * perp);
  };

  int tipX, tipY, wingLX, wingLY, wingRX, wingRY;
  int shaftTLX, shaftTLY, shaftTRX, shaftTRY;
  int shaftBLX, shaftBLY, shaftBRX, shaftBRY;

  pt( R - 2,    0,  tipX,     tipY);
  pt( R - 9,    5,  wingLX,   wingLY);
  pt( R - 9,   -5,  wingRX,   wingRY);
  pt( R - 9,    1,  shaftTLX, shaftTLY);
  pt( R - 9,   -1,  shaftTRX, shaftTRY);
  pt(-(R - 6),  1,  shaftBLX, shaftBLY);
  pt(-(R - 6), -1,  shaftBRX, shaftBRY);

  display.fillTriangle(tipX, tipY, wingLX, wingLY, wingRX, wingRY, SH110X_WHITE);
  display.fillTriangle(shaftTLX, shaftTLY, shaftTRX, shaftTRY, shaftBLX, shaftBLY, SH110X_WHITE);
  display.fillTriangle(shaftTRX, shaftTRY, shaftBLX, shaftBLY, shaftBRX, shaftBRY, SH110X_WHITE);

  display.display();
}

void loop() {
  drawCompass(readHeadingDeg());
  delay(50);
}
