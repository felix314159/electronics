// TLE4997E2 Pinout (you stare at branded side which shows 4997E2):
//     Pin 1: VDD (needs 5V)
//     Pin 2: GND
//     Pin 3: DATA (10k + 20k V divider)
// i also added a 100 nF capacitor between VDD and GND, placed close to the sensor
const int hallPin = A0; // use analog pin 0 on giga

// -------- ADC / board settings --------
const float ADC_REF_V = 3.3f;      // GIGA ADC reference domain
const int ADC_MAX = 65535;         // 16-bit read resolution

// -------- Divider values --------
// Measure these with your multimeter and replace the example values.
const float RTOP_OHM = 10000.0f;   // resistor from sensor OUT to A0
const float RBOT_OHM = 20000.0f;   // resistor from A0 to GND

// -------- Sensor sensitivity --------
// Choose based on your actual TLE4997E2 configuration.
//   60.0 mV/mT  -> typical final-test calibrated samples
const float SENS_MV_PER_MT = 60.0f;

// -------- Averaging / calibration --------
const int SAMPLE_COUNT = 200;
const int ZERO_CAL_SAMPLES = 2000;   // total samples used at startup for zero calibration

float zeroVout = 0.0f;   // calibrated no-field output voltage at sensor pin

float readAverageRaw(int n) {
  uint32_t sum = 0;
  for (int i = 0; i < n; i++) {
    sum += analogRead(hallPin);
  }
  return sum / (float)n;
}

float rawToVA0(float raw) {
  return raw * ADC_REF_V / ADC_MAX;
}

float vA0ToVout(float vA0) {
  return vA0 * (RTOP_OHM + RBOT_OHM) / RBOT_OHM;
}

void calibrateZeroField() {
  Serial.println("Calibrating zero field...");
  Serial.println("Keep magnet away and sensor mechanically still.");

  const int chunk = 100;
  int loops = ZERO_CAL_SAMPLES / chunk;
  if (loops < 1) loops = 1;

  float acc = 0.0f;
  for (int i = 0; i < loops; i++) {
    float raw = readAverageRaw(chunk);
    float vA0 = rawToVA0(raw);
    float vOut = vA0ToVout(vA0);
    acc += vOut;
    delay(5);
  }

  zeroVout = acc / loops;

  Serial.print("Zero-field baseline Vout = ");
  Serial.print(zeroVout, 6);
  Serial.println(" V");
  Serial.println();
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(16); // use 16 bit resolution of giga, tho sensor says 12 bit accuracy so it doesn't matter much

  delay(1500);
  calibrateZeroField();
}

void loop() {
  float raw = readAverageRaw(SAMPLE_COUNT);
  float vA0 = rawToVA0(raw);
  float vOut = vA0ToVout(vA0);

  // Signed signal around zero-field baseline
  float fieldSignalV = vOut - zeroVout;
  float field_mT = (fieldSignalV * 1000.0f) / SENS_MV_PER_MT;
  float field_G  = field_mT * 10.0f;   // 1 mT = 10 gauss

  if (field_mT < 0.1) {
    field_mT = 0;
  }

  if (field_G < 0.1) {
    field_G = 0;
  }

  Serial.print("raw_avg=");
  Serial.print(raw, 1);

  Serial.print("  A0=");
  Serial.print(vA0, 2);
  Serial.print(" V");

  Serial.print("  sensor OUT=");
  Serial.print(vOut, 2);
  Serial.print(" V");

  Serial.print("  fieldSignal=");
  Serial.print(fieldSignalV, 6);
  Serial.print(" V");

  Serial.print("  B=");
  Serial.print(field_mT, 4);
  Serial.print(" mT");

  Serial.print("  (");
  Serial.print(field_G, 2);
  Serial.println(" G)");

  delay(200);
}