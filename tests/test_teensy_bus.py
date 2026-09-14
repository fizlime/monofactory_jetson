import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mono_press.application import Application
from mono_press.units.teensy_bus import TeensyBusController
from mono_press.units.servo42c_protocol import move_body


class USB:
    def __init__(self,*args,**kwargs):
        self.is_open=True;self.data=bytearray();self.condition=threading.Condition();self.commands=[];self.auto=True
    def emit(self,text):
        with self.condition:self.data.extend((text+'\n').encode());self.condition.notify_all()
    def reset_input_buffer(self):self.data.clear()
    def write(self,data):
        f=data.decode().split()
        if f[0]=='HELLO':self.emit('HELLO '+f[1]+' 1')
        elif f[0]=='PING':self.emit('PONG')
        elif f[0]=='CMD':
            self.commands.append(f)
            if self.auto:
                if f[3]=='3a':self.emit(f'R {f[1]} {f[2]} PROBE 0')
                else:self.emit(f'R {f[1]} {f[2]} STATUS '+('2' if f[3].startswith('fd') else '1'))
        return len(data)
    def read(self,size):
        with self.condition:
            if not self.data:self.condition.wait(.01)
            data=bytes(self.data[:size]);del self.data[:size];return data
    def close(self):self.is_open=False


class TeensyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=Application(Path(self.tmp.name),'/dev/not-used',38400,True)
        self.usb=USB()
        p=patch('mono_press.units.teensy_bus.serial.Serial',return_value=self.usb);p.start();self.addCleanup(p.stop)
        self.bus=TeensyBusController(self.app.state,'/dev/ttyACM0',115200,False,config_store=self.app.config)
        self.bus.bind(0xe0,'P01_E0');self.addCleanup(self.bus.close)
        self.assertTrue(self.bus.connect())

    def test_handshake_does_not_enable_or_move(self):
        self.assertEqual(self.usb.commands,[])
        self.assertTrue(self.bus.probe(0xe0)[0])
        self.assertFalse(self.bus.probe_status[0xe0]['enabled'])
        self.assertEqual(self.usb.commands[0][3],'3a')

    def test_completion_without_start_and_signed_distance(self):
        body=move_body(-3200,16)
        ok,detail,token=self.bus.command(0xe0,body,'MOVE',.3)
        self.assertTrue(ok);self.assertEqual(detail,'COMPLETE')
        self.assertEqual(self.usb.commands[-1][3],'fd9000000c80')
        self.assertEqual(len(self.usb.commands),1,'No move retry')

    def test_three_axes_send_before_any_ack(self):
        self.usb.auto=False;results=[]
        threads=[threading.Thread(target=lambda a=a:results.append(self.bus.command(a,[0xf3,1],'ENABLE',1))) for a in (0xe0,0xe1,0xe2)]
        for t in threads:t.start()
        deadline=time.monotonic()+.5
        while len(self.usb.commands)<3 and time.monotonic()<deadline:time.sleep(.005)
        self.assertEqual(len(self.usb.commands),3)
        for f in self.usb.commands:self.usb.emit(f'R {f[1]} {f[2]} STATUS 1')
        for t in threads:t.join(1)
        self.assertTrue(all(r[0] for r in results));self.assertEqual(len(results),3)

    def test_request_ids_reject_late_other_axis_and_duplicate_replies(self):
        self.usb.auto=False;token=self.bus._request(0xe0,[0xfd,16,0,0,12,128],1)
        self.bus._parse_line(f'R {token-1} 0 STATUS 2')
        self.bus._parse_line(f'R {token} 1 STATUS 2')
        self.assertEqual(self.bus.requests[token]['events'],[])
        self.bus._parse_line(f'R {token} 0 STATUS 2');self.bus._parse_line(f'R {token} 0 STATUS 2')
        self.assertEqual(len(self.bus.requests[token]['events']),1)

    def test_failure_after_start_returns_promptly_and_stop_preserves_reason(self):
        self.usb.auto=False;token=self.bus._request(0xe0,[0xfd,16,0,0,12,128],1)
        self.bus._parse_line(f'R {token} 0 STATUS 1');self.bus._parse_line(f'R {token} 0 ERROR COMMAND_REJECTED')
        before=time.monotonic();self.assertIsNone(self.bus.wait_event(0xe0,2,token,10))
        self.assertLess(time.monotonic()-before,.1)
        self.bus.emergency_stop(0xe0)
        self.assertEqual(self.bus.failure_detail(0xe0,token),'COMMAND_REJECTED')

    def test_stop_preempts_inflight_transaction_without_command_lock(self):
        self.usb.auto=False;result=[]
        thread=threading.Thread(target=lambda:result.append(self.bus.command(0xe0,[0xfd,16,0,0,12,128],'MOVE',2)))
        thread.start()
        deadline=time.monotonic()+.5
        while not self.usb.commands and time.monotonic()<deadline:time.sleep(.005)
        self.assertTrue(self.bus.emergency_stop(0xe0));thread.join(.5)
        self.assertFalse(thread.is_alive());self.assertEqual(result[0][1],'CANCELLED')
        self.assertEqual(self.usb.commands[-1][3],'f7')

    def test_disconnect_never_reports_completion(self):
        self.usb.auto=False;token=self.bus._request(0xe0,[0xfd,16,0,0,12,128],1)
        self.bus._link_failed('USB_LOST')
        self.assertIsNone(self.bus.wait_event(0xe0,2,token,1))
        self.assertFalse(self.app.state.units['P01_E0']['enabled'])

    def test_factory_preserves_cycle_composition_and_excluded_e3(self):
        config=self.app.config.snapshot();config['p01']['transport']='teensy_usb';config['p01']['axes']['E3']['installed']=False
        self.app.config.save(config)
        other=Application(Path(self.tmp.name),'/dev/ttyTHS1',38400,True)
        self.addCleanup(other.bus.close)
        self.assertIsInstance(other.bus,TeensyBusController)
        self.assertEqual([s.code for s in other.sequences],[s.code for s in self.app.sequences])
        self.assertFalse(other.units.p01_axes[3].installed())

if __name__=='__main__':unittest.main()
