#include <Arduino.h>
#include <cassert>
#include <iostream>
unsigned long testNow=0;
int testPins[20],testModes[20];
TestSerial Serial;
#include "../../firmware/p06_relays/p06_relays.h"
int main() {
  for(int i=0;i<20;i++) { testPins[i]=9;testModes[i]=9; }
  p06_begin();
  for(int i=0;i<20;i++) {
    if(i>=8&&i<=10) { assert(testPins[i]==!P06_RELAY_ON_LEVEL);assert(testModes[i]==OUTPUT); }
    else { assert(testPins[i]==9);assert(testModes[i]==9); }
  }
  std::string level=P06Relay::levelName();
  Serial.send("P06 SET 1 "+level+" 7");p06_poll();assert(P06Relay::mask==0);
  Serial.send("\n");p06_poll();assert(P06Relay::mask==7);
  for(int i=8;i<=10;i++)assert(testPins[i]==P06_RELAY_ON_LEVEL);
  assert(Serial.output.find("P06 ACK 1 "+level+" 7")!=std::string::npos);
  Serial.send("P06 SET 2 "+std::string(level=="LOW"?"HIGH":"LOW")+" 0\n");p06_poll();assert(P06Relay::mask==7);
  Serial.send("P06 SET 2 "+level+" 8\n");p06_poll();assert(P06Relay::mask==7);
  Serial.send("P06 SET 2 "+level+" 1 extra\n");p06_poll();assert(P06Relay::mask==7);
  Serial.send("P06 SET 2 "+level+" 1\n");p06_poll();assert(P06Relay::mask==1);
  assert(testPins[8]==P06_RELAY_ON_LEVEL&&testPins[9]==!P06_RELAY_ON_LEVEL&&testPins[10]==!P06_RELAY_ON_LEVEL);
  testNow=2000;Serial.send("P06 KEEP 2\n");p06_poll();
  testNow=4000;p06_poll();assert(P06Relay::mask==1);
  Serial.send("P06 KEEP 99\n");p06_poll();
  testNow=5001;p06_poll();assert(P06Relay::mask==0);
  Serial.send(std::string(80,'x')+"\nP06 SET 3 "+level+" 4\n");p06_poll();assert(P06Relay::mask==4);
  Serial.send("P06 OFF\n");p06_poll();assert(P06Relay::mask==0);
  std::cout<<level<<": boot OFF, independent pins, strict commands, heartbeat timeout and recovery OK\n";
}
