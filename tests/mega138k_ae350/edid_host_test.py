"""Exercise the real BSP EDID reader against a synthetic DDC sink."""
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from gateware.machines.mega138k_ae350.address_map import AddressMap
from gateware.description import resolve_machine
ROOT = Path(__file__).resolve().parents[2]
class EDIDTest(unittest.TestCase):
    def test_edid(self):
        bsp = ROOT / 'firmware/bsp/mega138k_ae350'
        with tempfile.TemporaryDirectory() as directory:
            exe = str(Path(directory) / 'edid')
            (Path(directory)/'ae350_address_map.h').write_text(AddressMap(resolve_machine('mega138k_ae350',ROOT)).c_header())
            subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                '-I'+directory, '-I'+str(bsp/'video'), '-I'+str(bsp/'io'),
                str(bsp/'video/ae350_edid.c'), str(Path(__file__).with_suffix('.c')),
                '-o', exe], check=True)
            subprocess.run([exe], check=True, timeout=10)
            subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                '-I'+directory, '-I'+str(bsp/'io'),
                str(Path(__file__).with_name('edid_ddc_host_test.c')), '-o', exe], check=True)
            subprocess.run([exe], check=True, timeout=10)
if __name__ == '__main__': unittest.main()
