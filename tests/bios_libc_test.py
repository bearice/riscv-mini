"""Run the actual Newlib app/runtime against the BIOS ecall filesystem model."""
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import doom_sim

@unittest.skipUnless(doom_sim.find_qemu() and (ROOT/'.tools.local.json').exists(), 'RISC-V compiler/QEMU required')
class LibcTest(unittest.TestCase):
    def test_app_contracts_and_exit_cleanup(self):
        out=ROOT/'build/bios-libc-test'
        out.mkdir(parents=True,exist_ok=True)
        wad=out/'fixture.wad'
        wad.write_bytes(b'IWAD'+bytes(range(256))*32)
        elf=doom_sim.build(out,wad,soft_float=True,
                          sources=[ROOT/'firmware/examples/libc_test.c'])
        code,text,_,timed_out=doom_sim.run(elf,doom_sim.find_qemu(),out,30,[])
        self.assertFalse(timed_out,text[-1500:])
        self.assertEqual(code,0,text[-1500:])
        self.assertIn('LIBC contracts OK',text)
        self.assertIn('LIBC atexit OK',text)
        self.assertIn('LIBC TEST PASS',text)
        self.assertNotIn('LIBC FAIL',text)
        self.assertNotIn('SIM TRAP',text)

if __name__=='__main__':
    unittest.main()
