"""Gowin may return success even when programming or verification failed."""
import subprocess
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from boot_upload import programmer_succeeded

class ProgrammerResultTests(unittest.TestCase):
    def result(self,text,code=0,stderr=''):
        return subprocess.CompletedProcess([],code,text,stderr)
    def test_real_zero_exit_verify_failure(self):
        self.assertFalse(programmer_succeeded(self.result('Error: SPI Verify failed!\n Finished.\n')))
    def test_success_and_other_failures(self):
        self.assertTrue(programmer_succeeded(self.result('Verify... 100%\n Finished.\n')))
        self.assertTrue(programmer_succeeded(self.result('SPI end of address: 0x0DD800\n Finished!\n')))
        self.assertFalse(programmer_succeeded(self.result('Finished.',1)))
        self.assertFalse(programmer_succeeded(self.result('Finished.',stderr='Error: no cable')))
        self.assertFalse(programmer_succeeded(self.result('Programming...')))

if __name__=='__main__':unittest.main()
