#include "Arduino.h"
#include <cassert>
#include <iostream>
uint32_t nowMs=0;
HardwareSerial Serial,Serial2,Serial3,Serial4,Serial5;
#include "../../firmware/teensy_p01/teensy_p01.ino"
void host(const std::string &s){Serial.receive(s+"\n");loop();}
void motor(int i,std::initializer_list<uint8_t> bytes){for(auto b:bytes)axes[i].uart->input.push_back(b);loop();}
int main(){
  setup();assert(Serial3.baud==38400&&Serial2.baud==38400);
  for(auto &a:axes)assert(a.uart->output.empty());
  host("CMD 1 0 fd1000000c80 6000");assert(Serial3.output.empty());
  Serial.connected=false;
  host("HELLO 123");assert(Serial.output.find("HELLO 123 1")!=std::string::npos);
  host("PING");assert(session&&Serial.output.find("PONG")!=std::string::npos);
  Serial.connected=true;
  host("CMD 2 0 f301 1500");host("CMD 3 1 f301 1500");host("CMD 4 2 f301 1500");
  assert(Serial3.output==std::string("\xe0\xf3\x01\xd4",4));
  assert(Serial4.output==std::string("\xe1\xf3\x01\xd5",4));
  assert(Serial2.output==std::string("\xe2\xf3\x01\xd6",4));assert(Serial5.output.empty());
  motor(0,{0xe0,1,0xe1});motor(1,{0xe1,1,0xe2});motor(2,{0xe2,1,0xe3});
  assert(Serial.output.find("R 2 0 STATUS 1")!=std::string::npos);
  host("CMD 5 0 fd9000000c80 6000");assert(Serial3.output.substr(4)==std::string("\xe0\xfd\x90\0\0\x0c\x80\xf9",8));
  host("CMD 6 0 fd1000000c80 6000");assert(Serial.output.find("R 6 0 ERROR BUSY")!=std::string::npos);
  motor(0,{0xe0,2,0xe2});assert(Serial.output.find("R 5 0 STATUS 2")!=std::string::npos);
  host("CMD 7 3 f301 1500");motor(3,{0xe3,1,0,0xe4});assert(Serial.output.find("R 7 3 STATUS 1")!=std::string::npos);
  host("CMD 8 0 f301 1500");motor(0,{0xe0,1,0,0xe1});assert(axes[0].request==8);motor(0,{0xe0,1,0xe1});
  host("CMD 9 0 fd1000000c80 6000");host("CMD 10 0 f7 1500");
  assert(Serial.output.find("R 9 0 ERROR CANCELLED")!=std::string::npos);
  assert(Serial.output.find("R 10 0 STATUS 1")==std::string::npos);
  motor(0,{0xe0,2,0xe2});assert(axes[0].request==10);motor(0,{0xe0,1,0xe1});
  nowMs+=100;host("CMD 11 0 fd1000000c80 6000");nowMs+=2100;loop();
  assert(!session&&axes[0].request==0);assert(Serial.output.find("R 11 0 ERROR LINK_LOST")!=std::string::npos);
  nowMs+=100;host("HELLO 124");host("CMD 12 1 3a 1500");motor(1,{0xe1,2,0xe3});
  assert(Serial.output.find("R 12 1 PROBE 0")!=std::string::npos);
  host("CMD 13 2 fd1000000c80 100");nowMs+=101;loop();
  assert(Serial.output.find("R 13 2 ERROR TIMEOUT")!=std::string::npos);
  std::cout<<"PASS firmware: no boot motion, independent routing, padding, busy guard, completion, STOP and watchdog\n";
}
