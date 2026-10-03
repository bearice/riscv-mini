import io
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from boot_upload import BootSession
class Port:
    def __init__(self,data):self.data=iter(data)
    def read(self,n):
        try:return bytes([next(self.data)])
        except StopIteration:raise AssertionError("Read past terminal response")
class BootSessionTest(unittest.TestCase):
    def test_ddr_failure_stops_wait_and_preserves_log(self):
        data=b"\r\nERR DDR INIT/TIMEOUT; reset\r\n";log=io.BytesIO()
        with self.assertRaisesRegex(RuntimeError,"DDR initialization"):
            BootSession(Port(data),log).until(b"BOOT SELECT:")
        self.assertEqual(log.getvalue(),data)
    def test_success_marker(self):
        self.assertEqual(BootSession(Port(b"ok BL> ")).until(b"BL> "),b"ok BL> ")
if __name__=='__main__':unittest.main()
