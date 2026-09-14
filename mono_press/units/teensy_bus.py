"""P01 USB bridge: one independently addressed Teensy UART per Servo42C."""
import secrets
import threading
import time

from .servo_bus import ServoBusController, serial


class TeensyBusController(ServoBusController):
    independent_axes = True

    def __init__(self, state, port, baud, simulation=False, *, config_store=None):
        super().__init__(state, port, baud, simulation)
        self.config_store = config_store
        self.tx_lock = threading.RLock()
        self.axis_locks = {address: threading.RLock() for address in self.events}
        self.requests = {}
        self.request_id = secrets.randbelow(1_000_000_000) + 1
        self.last_seen = 0.0

    def connect(self):
        if self.simulation:
            return super().connect()
        with self.connect_lock:
            if self.device and self.device.is_open and self.reader_thread and self.reader_thread.is_alive() and self.state.serial.get('connected'):
                return True
            if serial is None:
                self.state.update_serial(connected=False, last_error='pyserial이 설치되지 않았습니다.')
                return False
            try:
                self.reader_stop.set()
                if self.reader_thread and self.reader_thread is not threading.current_thread():
                    self.reader_thread.join(timeout=1)
                    if self.reader_thread.is_alive():
                        raise RuntimeError('이전 Teensy 수신 스레드 종료 대기 중')
                if self.device:self.device.close()
                self.device = serial.Serial(self.port, self.baud, timeout=.02, write_timeout=.25, exclusive=True)
                self.device.reset_input_buffer()
                nonce = str(secrets.randbits(32))
                self.device.write(('HELLO '+nonce+'\n').encode('ascii'))
                deadline = time.monotonic()+1.5
                buffer = bytearray()
                matched = False
                while time.monotonic()<deadline:
                    buffer.extend(self.device.read(256))
                    while b'\n' in buffer:
                        line, _, rest = buffer.partition(b'\n');buffer=bytearray(rest)
                        if line.strip()==('HELLO '+nonce+' 1').encode():matched=True;break
                    if matched:break
                    if len(buffer)>512:buffer.clear()
                if not matched:raise RuntimeError('Teensy P01 bridge v1 펌웨어 응답 없음')
                self.reader_stop.clear()
                self.last_seen=time.monotonic()
                self.state.update_serial(port=self.port, baud=self.baud, connected=True, last_error='', transport='teensy_usb', rx_mode='teensy_independent_uart')
                self.reader_thread=threading.Thread(target=self._reader_loop,name='teensy-p01-rx',daemon=True)
                self.reader_thread.start()
                self.state.record('SERIAL','Teensy P01 USB 연결 · E0=Serial3/E1=Serial4/E2=Serial2/E3=Serial5','P01')
                return True
            except Exception as exc:
                if self.device:self.device.close()
                self.device=None
                self._link_failed(str(exc))
                return False

    def _link_failed(self, detail):
        self.state.update_serial(connected=False,last_error=detail)
        with self.event_condition:
            for request in self.requests.values():
                if not request.get('done') and not request.get('error'):request['error']=detail
            self.event_condition.notify_all()
        for unit in self.unit_by_address.values():
            self.state.update_unit(unit,connected=False,enabled=False,homed=False,error=detail)

    def _send(self, line):
        with self.tx_lock:
            packet=(line+'\n').encode('ascii')
            if not self.device or self.device.write(packet)!=len(packet):raise OSError('Teensy USB 전송 실패')

    def _reader_loop(self):
        buffer=bytearray();ping_at=0
        try:
            while not self.reader_stop.is_set():
                now=time.monotonic()
                if now-ping_at>=.4:self._send('PING');ping_at=now
                if now-self.last_seen>2:raise OSError('Teensy heartbeat 시간 초과')
                buffer.extend(self.device.read(256))
                while b'\n' in buffer:
                    line, _, rest=buffer.partition(b'\n');buffer=bytearray(rest)
                    self._parse_line(line.decode('ascii',errors='replace').strip())
                if len(buffer)>512:buffer.clear()
        except Exception as exc:
            if not self.reader_stop.is_set():
                self._link_failed(str(exc));self.state.record('ERROR',str(exc),'P01')

    def _parse_line(self, line):
        if line=='PONG':self.last_seen=time.monotonic();return
        fields=line.split()
        if len(fields)<5 or fields[0]!='R':return
        try:request_id,axis=int(fields[1]),int(fields[2])
        except ValueError:return
        with self.event_condition:
            request=self.requests.get(request_id)
            if not request or request['address']!=0xE0+axis or request.get('error') or request.get('done'):return
            kind,value=fields[3],fields[4]
            if kind=='STATUS' and value in ('0','1','2'):
                status=int(value)
                if status==0:request['error']='COMMAND_REJECTED'
                else:request['events'].append({'status':status,'at':time.monotonic()})
                if status==2 or (status==1 and request['opcode']!=0xFD):request['done']=True
                label={0:'FAIL',1:'START' if request['opcode']==0xFD else 'ACK',2:'COMPLETE'}[status]
            elif kind=='PROBE' and value in ('0','1'):
                request['enabled']=value=='1';request['done']=True;label='EN='+value
            elif kind=='ERROR':request['error']=value;label=value
            else:return
            self.last_seen=time.monotonic()
            self.event_condition.notify_all()
        unit=self.unit_by_address.get(0xE0+axis)
        if unit:self.state.update_unit(unit,connected=not request.get('error'),last_event=label,last_ack=time.time())
        self.state.record('ERROR' if request.get('error') else 'RX',f'E{axis} {label}','P01',unit)

    def _request(self, address, body, timeout, cancel=None):
        if address not in self.events:raise ValueError('Teensy 축 주소 오류')
        if not self.connect():return None
        with self.tx_lock:
            if cancel and cancel.is_set():return None
            return self._send_request(address,body,timeout)

    def _send_request(self, address, body, timeout):
        with self.event_condition:
            if body[0]==0xF7:
                for request in self.requests.values():
                    if request['address']==address and not request.get('done') and not request.get('error'):request['error']='CANCELLED'
                self.event_condition.notify_all()
            self.request_id=self.request_id%0xFFFFFFFE+1
            token=self.request_id
            self.requests[token]={'address':address,'opcode':body[0],'events':[]}
            for old in list(self.requests)[:-128]:
                if self.requests[old].get('done') or self.requests[old].get('error'):del self.requests[old]
        if body[0]==0xFD:
            pulses=int.from_bytes(bytes(body[2:6]),'big');speed=body[1]&0x7F
            config=self.config_store.snapshot()['p01'] if self.config_store else {}
            timeout=max(timeout,config.get('move_timeout',6),pulses/(max(speed,1)*500)*1.5+2)
        milliseconds=min(0x7FFFFFFE,max(1500,round(timeout*1000)))
        try:
            self._send(f'CMD {token} {address-0xE0} {bytes(body).hex()} {milliseconds}')
            self.state.record('TX',f'E{address-0xE0} Teensy {bytes(body).hex(" ")}', 'P01',self.unit_by_address.get(address))
            return token
        except Exception as exc:self._link_failed(str(exc));return None

    def wait_event(self, address, expected, after, timeout, cancel=None):
        if self.simulation:return super().wait_event(address,expected,after,timeout,cancel)
        allowed=set(expected) if isinstance(expected,(tuple,list,set)) else {expected}
        deadline=time.monotonic()+timeout
        with self.event_condition:
            while True:
                request=self.requests.get(after)
                if not request or request.get('error') or (cancel and cancel.is_set()):return None
                for event in request['events']:
                    if event['status'] in allowed:return event
                remaining=deadline-time.monotonic()
                if remaining<=0 or self.reader_stop.is_set():return None
                self.event_condition.wait(min(.05,remaining))

    def failure_detail(self, address, token):
        return self.requests.get(token,{}).get('error','')

    def command(self, address, body, label, timeout, cancel=None):
        if self.simulation:return super().command(address,body,label,timeout,cancel)
        with self.axis_locks[address]:
            if cancel and cancel.is_set():return False,'CANCELLED',0
            token=self._request(address,body,timeout,cancel)
            if token is None:return False,self.state.serial.get('last_error','NO_RESPONSE'),0
            event=self.wait_event(address,(1,2) if body[0]==0xFD else 1,token,timeout,cancel)
            if event is None:return False,self.failure_detail(address,token) or ('CANCELLED' if cancel and cancel.is_set() else 'NO_RESPONSE'),token
            return True,'COMPLETE' if event['status']==2 else 'ACK',token

    def probe(self, address, timeout=.7):
        if self.simulation:return True,'SIM 응답'
        with self.axis_locks[address]:
            token=self._request(address,[0x3A],timeout)
            if token is None:return False,self.state.serial.get('last_error','NO_RESPONSE')
            deadline=time.monotonic()+timeout
            with self.event_condition:
                while not self.requests[token].get('done') and not self.requests[token].get('error'):
                    remaining=deadline-time.monotonic()
                    if remaining<=0:return False,'NO_RESPONSE'
                    self.event_condition.wait(min(.05,remaining))
                request=self.requests[token]
            if request.get('error'):return False,request['error']
            enabled=request['enabled'];self.probe_status[address]={'enabled':enabled}
            return True,f'Teensy 독립 UART 응답 · {"활성" if enabled else "비활성"}'

    def emergency_stop(self,address):
        if self.simulation:return super().emergency_stop(address)
        return self._request(address,[0xF7],1.5) is not None

    def close(self):
        self._link_failed('Teensy 연결 종료')
        super().close()
