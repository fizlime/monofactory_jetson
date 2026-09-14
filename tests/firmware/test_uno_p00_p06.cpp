#include <Arduino.h>
#include <cassert>
#include <iostream>
unsigned long testNow=0;
int testPins[20],testModes[20];
TestSerial Serial;
#include "../../firmware/mono_uno_p00_p06/mono_uno_p00_p06.ino"
int main() {
  for(int i=0;i<20;i++){testPins[i]=9;testModes[i]=9;}
  testPins[2]=LOW;testPins[3]=HIGH;testPins[4]=LOW;testPins[5]=HIGH;
  setup();
  assert(Serial.baud==115200);
  for(int i=2;i<=5;i++)assert(testModes[i]==INPUT_PULLUP);
  for(int i=8;i<=10;i++)assert(testModes[i]==OUTPUT&&testPins[i]==LOW);
  for(int i:{0,1,6,7,11,12,13})assert(testModes[i]==9&&testPins[i]==9);
  assert(Serial.output.find("센서 1: 감지 | 센서 2: 미감지 | 센서 3: 감지 | 센서 4: 미감지")!=std::string::npos);
  assert(Serial.output.find("P06 STATE 0 HIGH 0")!=std::string::npos);
  Serial.output.clear();Serial.send("P06 SET 1 HIGH 5\n");loop();
  assert(P06Relay::mask==5&&testPins[8]==HIGH&&testPins[9]==LOW&&testPins[10]==HIGH);
  testNow=1000;testPins[3]=LOW;testPins[5]=LOW;loop();
  assert(Serial.output.find("센서 1: 감지 | 센서 2: 감지 | 센서 3: 감지 | 센서 4: 감지")!=std::string::npos);
  testNow=3001;loop();
  assert(P06Relay::mask==0);
  for(int i=8;i<=10;i++)assert(testPins[i]==LOW);
  Serial.send("P06 SET 2 LOW 7\n");loop();assert(P06Relay::mask==0);
  Serial.send("P06 SET 3 HIGH 0\n");loop();
  assert(Serial.output.find("P06 ACK 3 HIGH 0")!=std::string::npos);
  std::cout<<"PASS: original sensor polarity/format, pin isolation, boot OFF, HIGH relay commands and watchdog\n";
}
