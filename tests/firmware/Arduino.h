#pragma once
#include <stdint.h>
#include <string>
#include <sstream>
#include <deque>
#define LOW 0
#define HIGH 1
#define OUTPUT 1
#define INPUT_PULLUP 2
#define F(value) value
extern unsigned long testNow;
extern int testPins[20], testModes[20];
inline unsigned long millis() { return testNow; }
inline void digitalWrite(uint8_t pin, int level) { testPins[pin]=level; }
inline void pinMode(uint8_t pin, int mode) { testModes[pin]=mode; }
inline int digitalRead(uint8_t pin) { return testPins[pin]; }
struct TestSerial {
  unsigned long baud=0;
  void begin(unsigned long value) { baud=value; }
  std::deque<char> input;
  std::string output;
  int available() { return input.size(); }
  int read() { char c=input.front(); input.pop_front(); return c; }
  template<class T> void print(T value) { std::ostringstream s; s<<value; output+=s.str(); }
  void print(uint8_t value) { print(static_cast<unsigned int>(value)); }
  template<class T> void println(T value) { print(value); output+='\n'; }
  void println() { output+='\n'; }
  void send(const std::string &text) { for(char c:text) input.push_back(c); }
};
extern TestSerial Serial;
