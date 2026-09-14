#include <Arduino.h>

// Match the current P06 web setting. HIGH energizes the relay; LOW is OFF.
#define P06_RELAY_ON_LEVEL HIGH
#include "p06_relays.h"

const uint8_t SENSOR_PINS[4] = {2, 3, 4, 5};
unsigned long lastSensorReport = 0;

void reportSensors() {
  for (uint8_t i = 0; i < 4; ++i) {
    Serial.print(F("센서 "));
    Serial.print(i + 1);
    Serial.print(F(": "));
    // Preserve the original Uno firmware: INPUT_PULLUP, LOW = detected.
    Serial.print(digitalRead(SENSOR_PINS[i]) == LOW ? F("감지") : F("미감지"));
    if (i < 3) Serial.print(F(" | "));
  }
  Serial.println();
}

void setup() {
  Serial.begin(115200);
  for (uint8_t i = 0; i < 4; ++i) pinMode(SENSOR_PINS[i], INPUT_PULLUP);
  Serial.println(F("센서 4개 확인 시작"));
  p06_begin();  // D8/D9/D10 start OFF before enabling the output drivers.
  reportSensors();
  lastSensorReport = millis();
}

void loop() {
  p06_poll();  // Nonblocking command handling and 3-second relay watchdog.
  const unsigned long now = millis();
  if (static_cast<unsigned long>(now - lastSensorReport) >= 1000UL) {
    reportSensors();
    lastSensorReport = now;
  }
}
