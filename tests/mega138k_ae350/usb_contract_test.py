"""Enumeration evidence must not imply physical keyboard input."""
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from mini_ops.mega138k_ae350.ae350_usb_suite import judge_usb

class USBContractTests(unittest.TestCase):
    def test_device_and_hid_required(self):
        for text in ('USB ENUM PASS\r\n','USB BEGIN\r\nUSB DEVICE 046dc52b\r\nUSB ENUM PASS\r\n'):
            self.assertTrue(judge_usb(text)['failures'])
    def test_status_retains_identity_but_not_physical_acceptance(self):
        r=judge_usb('USB BEGIN\r\nUSB STATUS 00000001 VIDPID 046dc52b HID 00000003 REPORTS 00000004\r\nUSB ENUM PASS\r\n')
        self.assertFalse(r['failures']);self.assertEqual(r['devices'],['046dc52b'])
        self.assertEqual(r['hid_interfaces'],3);self.assertEqual(r['reports_received'],4)
        self.assertFalse(r['physical_keypress_verified'])
    def test_failure_or_incomplete_run_rejected(self):
        self.assertTrue(judge_usb('USB BEGIN\r\nUSB DEVICE 046dc52b\r\nUSB HID\r\nUSB ENUM FAIL\r\n')['failures'])
if __name__=='__main__':unittest.main()
