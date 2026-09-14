#include <Arduino.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

// Adapted from the user's proven independent-UART Servo42C sketch.
// USB commands drive individual operations; setup/loop never start a cycle.
const uint32_t SERVO_BAUD=38400;
const uint32_t HEARTBEAT_MS=2000;
struct Axis {
  HardwareSerial *uart;
  uint8_t addr;
  bool allowPadding;
  uint8_t parserState=0,rxStatus=0,expectedChecksum=0,paddingCount=0;
  uint32_t rxAt=0,request=0,started=0,timeout=0,quietUntil=0;
  uint8_t opcode=0;
};
// E0: RX15/TX14, E1: RX16/TX17, E2: RX7/TX8, E3: RX21/TX20 (spare).
Axis axes[]={{&Serial3,0xE0,false},{&Serial4,0xE1,false},{&Serial2,0xE2,false},{&Serial5,0xE3,true}};
uint32_t heartbeat=0;
bool session=false;
char hostLine[128];size_t hostLength=0;bool overflow=false;

uint8_t checksum(const uint8_t *data,size_t length){uint8_t sum=0;while(length--)sum+=*data++;return sum;}
void sendPacket(Axis &axis,const uint8_t *body,size_t length){
  axis.uart->write(axis.addr);axis.uart->write(body,length);
  axis.uart->write(uint8_t(axis.addr+checksum(body,length)));axis.uart->flush();
}
void reply(uint32_t request,int index,const char *kind,const char *value){
  Serial.print("R ");Serial.print(request);Serial.print(' ');Serial.print(index);
  Serial.print(' ');Serial.print(kind);Serial.print(' ');Serial.println(value);
}
void resetParser(Axis &a){a.parserState=0;a.paddingCount=0;}
void stopAxis(int i,const char *reason){
  Axis &a=axes[i];const uint8_t body[]={0xF7};sendPacket(a,body,1);
  if(a.request)reply(a.request,i,"ERROR",reason);
  a.request=0;a.quietUntil=millis()+75;resetParser(a);
}
void finish(int i,const char *kind,const char *value){
  Axis &a=axes[i];reply(a.request,i,kind,value);a.request=0;resetParser(a);
}
bool pollStatus(Axis &a,uint8_t &status){
  if(a.parserState && uint32_t(millis()-a.rxAt)>30)resetParser(a);
  while(a.uart->available()){
    uint8_t b=a.uart->read();a.rxAt=millis();
    switch(a.parserState){
      case 0:if(b==a.addr)a.parserState=1;break;
      case 1:
        if(b<=2){a.rxStatus=b;a.expectedChecksum=uint8_t(a.addr+b);a.paddingCount=0;a.parserState=2;}
        else{resetParser(a);if(b==a.addr)a.parserState=1;}
        break;
      case 2:
        if(b==a.expectedChecksum){status=a.rxStatus;resetParser(a);return true;}
        // Keep the user's E3-only padding rule: E3 01 00 E4 / E3 02 00 E5.
        if(a.allowPadding && b==0 && a.paddingCount<3)a.paddingCount++;
        else{resetParser(a);if(b==a.addr)a.parserState=1;}
        break;
    }
  }
  return false;
}
bool number(const char *s,uint32_t &v){
  if(!s||!*s||*s=='-'||*s=='+')return false;
  char *end;errno=0;unsigned long n=strtoul(s,&end,10);if(*end||errno==ERANGE||n>UINT32_MAX)return false;v=n;return true;
}
int nibble(char c){if(c>='0'&&c<='9')return c-'0';if(c>='a'&&c<='f')return c-'a'+10;if(c>='A'&&c<='F')return c-'A'+10;return -1;}
void handleLine(char *line){
  char *save=nullptr,*verb=strtok_r(line," ",&save);if(!verb)return;
  if(!strcmp(verb,"HELLO")){
    char *nonce=strtok_r(nullptr," ",&save);uint32_t n;
    if(!number(nonce,n)||strtok_r(nullptr," ",&save))return;
    for(int i=0;i<4;i++)if(axes[i].request)stopAxis(i,"NEW_SESSION");
    session=true;heartbeat=millis();Serial.print("HELLO ");Serial.print(nonce);Serial.println(" 1");return;
  }
  if(!strcmp(verb,"PING")&&session){heartbeat=millis();Serial.println("PONG");return;}
  if(strcmp(verb,"CMD")||!session)return;
  char *idText=strtok_r(nullptr," ",&save),*axisText=strtok_r(nullptr," ",&save);
  char *hex=strtok_r(nullptr," ",&save),*timeoutText=strtok_r(nullptr," ",&save);
  uint32_t id,index,timeout;
  if(!number(idText,id)||!id||!number(axisText,index)||index>3||!number(timeoutText,timeout)||!hex)return;
  if(strtok_r(nullptr," ",&save)||timeout<100||timeout>0x7FFFFFFE){reply(id,index,"ERROR","BAD_COMMAND");return;}
  size_t length=strlen(hex);uint8_t body[6];
  if(length%2||length>12||length<2){reply(id,index,"ERROR","BAD_COMMAND");return;}
  for(size_t j=0;j<length/2;j++){int a=nibble(hex[j*2]),b=nibble(hex[j*2+1]);if(a<0||b<0){reply(id,index,"ERROR","BAD_COMMAND");return;}body[j]=(a<<4)|b;}
  length/=2;uint8_t op=body[0];
  bool valid=(op==0xF3&&length==2&&body[1]<=1)||(op==0xF7&&length==1)||(op==0x3A&&length==1)||(op==0xFD&&length==6&&(body[1]&0x7F)&& (body[2]||body[3]||body[4]||body[5]));
  if(!valid){reply(id,index,"ERROR","BAD_COMMAND");return;}
  Axis &a=axes[index];
  if(op==0xF7){stopAxis(index,"CANCELLED");a.request=id;a.opcode=op;a.started=millis();a.timeout=timeout;return;}
  if(a.request||int32_t(millis()-a.quietUntil)<0){reply(id,index,"ERROR","BUSY");return;}
  while(a.uart->available())a.uart->read();
  resetParser(a);
  a.request=id;a.opcode=op;a.started=millis();a.timeout=timeout;
  sendPacket(a,body,length);
}
void setup(){
  Serial.begin(115200);
  for(auto &a:axes){a.uart->begin(SERVO_BAUD);while(a.uart->available())a.uart->read();}
  // No ENABLE, motion, blocking while(!Serial), or repeated forward/reverse here.
}
void loop(){
  // Bound USB parsing so motor status and watchdog are serviced under heavy input.
  for(int budget=0;budget<128&&Serial.available();budget++){
    char c=Serial.read();
    if(c=='\n'){
      if(!overflow){hostLine[hostLength]=0;handleLine(hostLine);}
      hostLength=0;overflow=false;
    }else if(c!='\r'){
      if(hostLength<sizeof(hostLine)-1&&!overflow)hostLine[hostLength++]=c;else overflow=true;
    }
  }
  // Teensy USB operator bool() can stay false briefly after port opening even
  // when HELLO was already received. Use the heartbeat for disconnect detection.
  if(session&&uint32_t(millis()-heartbeat)>HEARTBEAT_MS){
    for(int i=0;i<4;i++)if(axes[i].request)stopAxis(i,"LINK_LOST");
    session=false;
  }
  for(int i=0;i<4;i++){
    Axis &a=axes[i];uint8_t status;
    while(pollStatus(a,status)){
      if(!a.request)continue;
      if(a.opcode==0x3A){finish(i,status?"PROBE":"ERROR",status==1?"1":status==2?"0":"BAD_STATUS");}
      else if(!status){stopAxis(i,"COMMAND_REJECTED");}
      else if(a.opcode==0xFD){if(status==2)finish(i,"STATUS","2");else reply(a.request,i,"STATUS","1");}
      else if(status==1)finish(i,"STATUS","1");
    }
    if(a.request&&uint32_t(millis()-a.started)>a.timeout)stopAxis(i,"TIMEOUT");
  }
}
