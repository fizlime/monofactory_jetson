#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string>
#include <sstream>
#include <deque>
extern uint32_t nowMs;
inline uint32_t millis(){return nowMs;}
struct HardwareSerial {
  uint32_t baud=0;bool connected=true;
  std::deque<uint8_t> input;std::string output;
  operator bool()const{return connected;}
  void begin(uint32_t n){baud=n;}
  int available(){return input.size();}
  int read(){int b=input.front();input.pop_front();return b;}
  size_t write(uint8_t b){output+=char(b);return 1;}
  size_t write(const uint8_t *p,size_t n){output.append((const char*)p,n);return n;}
  void flush(){}
  template<class T>void print(T v){std::ostringstream s;s<<v;output+=s.str();}
  template<class T>void println(T v){print(v);output+='\n';}
  void receive(const std::string &s){for(unsigned char c:s)input.push_back(c);}
};
extern HardwareSerial Serial,Serial2,Serial3,Serial4,Serial5;
