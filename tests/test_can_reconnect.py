import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from mono_press.application import Application
from mono_press.units.can_press.can_link import CanError,select_adapter


class FakeLink:
    def __init__(self):
        self.usb_serial='CAN_TEST_01';self.disconnected=False;self.calls=[]
    def call(self,op):
        self.calls.append((op,threading.get_ident()))
        if self.disconnected:raise OSError('USB disconnected')
    def ready(self):self.call('ready')
    def sample(self):self.call('sample');return {'io':1,'position':100}
    def stop(self):self.call('stop')
    def close(self):self.calls.append(('close',threading.get_ident()))


class ReconnectTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=Application(Path(self.tmp.name),'/dev/not-used',38400,True)
        self.driver=self.app.units.press.can;self.driver.RECONNECT_SECONDS=.03
        self.available=False;self.links=[];self.attempts=[]
        def factory(config):
            self.attempts.append(config.usb_serial)
            if not self.available:raise OSError('USB not present')
            link=FakeLink();self.links.append(link);return link
        p=patch('mono_press.units.can_press.driver.CanLink',side_effect=factory)
        p.start();self.addCleanup(p.stop);self.addCleanup(self.driver.close)
    def until(self,test):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            if test():return
            time.sleep(.01)
        self.fail('reconnect deadline')
    def test_insert_after_startup_failure_connects_without_commands(self):
        self.assertFalse(self.driver.connect());self.available=True
        self.until(lambda:self.driver.connected)
        self.assertEqual(self.app.config.snapshot()['p04']['can']['usb_serial'],'CAN_TEST_01')
        self.assertFalse(self.driver.axis.snapshot['enabled'])
        self.assertFalse(self.driver.axis.snapshot['homed'])
        self.assertTrue(all(op in ('ready','sample') for op,_ in self.links[0].calls))
    def test_unplug_releases_stale_handle_and_reconnects_same_identity(self):
        self.available=True;self.assertTrue(self.driver.connect())
        old=self.links[0];self.driver.axis.update(homed=True,enabled=True)
        self.available=False;old.disconnected=True
        self.until(lambda:not self.driver.connected)
        self.assertFalse(self.driver.axis.snapshot['homed']);self.assertFalse(self.driver.axis.snapshot['enabled'])
        self.assertTrue(any(op=='close' for op,_ in old.calls))
        self.available=True;self.until(lambda:len(self.links)==2 and self.driver.connected)
        self.assertEqual(self.attempts[-1],'CAN_TEST_01')
        calls=[c for link in self.links for c in link.calls]
        self.assertEqual(len({tid for _,tid in calls}),1,'One USB owner for health and reconnect')
        self.assertTrue(all(op in ('ready','sample','close') for op,_ in calls))
    def test_close_stops_retry_and_does_not_restart_usb_worker(self):
        self.assertFalse(self.driver.connect());self.driver.close()
        n=len(self.attempts);time.sleep(.12)
        self.assertEqual(len(self.attempts),n);self.assertFalse(self.driver.thread.is_alive())
    def test_repeated_absence_does_not_flood_log(self):
        self.assertFalse(self.driver.connect());self.until(lambda:len(self.attempts)>=4)
        logs=[x for x in self.app.state.snapshot()['logs'] if x.get('process')=='P04' and x['type']=='ERROR']
        self.assertEqual(len(logs),1)
    def test_serial_selection_ignores_usb_bus_and_device_numbers(self):
        a=SimpleNamespace(serial_number='A',bus=1,address=2)
        b=SimpleNamespace(serial_number='B',bus=1,address=3)
        self.assertIs(select_adapter([a,b],'B'),b)
        b.bus=2;b.address=17;self.assertIs(select_adapter([b,a],'B'),b)
        with self.assertRaises(CanError):select_adapter([a],'B')
        with self.assertRaises(CanError):select_adapter([a,b])

if __name__=='__main__':unittest.main()
