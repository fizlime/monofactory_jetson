#pragma once
#include <Arduino.h>
#include <stdio.h>
#include <string.h>

// Define LOW or HIGH to match the relay module before including this header.
// No default: an unknown module must not be flashed with a guessed polarity.
#ifndef P06_RELAY_ON_LEVEL
#error "Define P06_RELAY_ON_LEVEL as LOW or HIGH after checking the relay module"
#endif
static_assert(P06_RELAY_ON_LEVEL == LOW || P06_RELAY_ON_LEVEL == HIGH, "Invalid relay level");

namespace P06Relay {
const uint8_t pins[3] = {8, 9, 10};  // GREEN, RED, BUZZER. Spare relays unused.
uint8_t mask = 0;
unsigned long sequence = 0, lastKeep = 0, lastReport = 0;
char buffer[64];
uint8_t length = 0;
bool overflow = false;

const char *levelName() { return P06_RELAY_ON_LEVEL == LOW ? "LOW" : "HIGH"; }
void outputs(uint8_t next) {
  mask = next;
  for (uint8_t i=0; i<3; ++i)
    digitalWrite(pins[i], (mask & (1 << i)) ? P06_RELAY_ON_LEVEL : !P06_RELAY_ON_LEVEL);
}
void report(const char *kind) {
  Serial.print("P06 "); Serial.print(kind); Serial.print(' ');
  Serial.print(sequence); Serial.print(' '); Serial.print(levelName());
  Serial.print(' '); Serial.println(mask);
}
void command(const char *line) {
  unsigned long seq;
  unsigned int requested;
  char level[8], extra;
  if (strcmp(line, "P06 OFF") == 0) { outputs(0); report("STATE"); return; }
  if (sscanf(line, "P06 SET %lu %7s %u %c", &seq, level, &requested, &extra) == 3) {
    if (!seq || seq > 2147483647UL || requested > 7 || strcmp(level, levelName()) != 0) {
      Serial.println("P06 ERR SETTINGS"); return;
    }
    sequence = seq; outputs(static_cast<uint8_t>(requested)); lastKeep = millis(); report("ACK"); return;
  }
  if (sscanf(line, "P06 KEEP %lu %c", &seq, &extra) == 1 && seq == sequence && mask) {
    lastKeep = millis(); return;
  }
  Serial.println("P06 ERR COMMAND");
}
}

inline void p06_begin() {
  P06Relay::outputs(0);  // Set the inactive latch before enabling the output pins.
  for (uint8_t i=0; i<3; ++i) pinMode(P06Relay::pins[i], OUTPUT);
  Serial.println("P06 READY 1"); P06Relay::report("STATE");
}

inline void p06_poll() {
  using namespace P06Relay;
  while (Serial.available() > 0) {
    char c = static_cast<char>(Serial.read());
    if (c == '\r' || c == '\n') {
      if (overflow) Serial.println("P06 ERR OVERFLOW");
      else if (length) { buffer[length] = '\0'; command(buffer); }
      length = 0; overflow = false;
    } else if (!overflow) {
      if (length < sizeof(buffer)-1) buffer[length++] = c;
      else overflow = true;
    }
  }
  unsigned long now = millis();
  if (mask && static_cast<unsigned long>(now-lastKeep) > 3000UL) {
    outputs(0); report("STATE");
  }
  if (static_cast<unsigned long>(now-lastReport) >= 1000UL) {
    report("STATE"); lastReport = now;
  }
}
