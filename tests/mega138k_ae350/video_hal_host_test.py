"""Compile the real C HAL/BIOS renderer with a controlled MMIO transport."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from gateware.machines.mega138k_ae350.address_map import AddressMap
from gateware.description import resolve_machine


class VideoHALTest(unittest.TestCase):
    def test_buffer_ownership_and_tty(self):
        cc=shutil.which('cc')
        self.assertTrue(cc,'host C compiler required in the Nix devshell')
        bsp=ROOT/'firmware/bsp/mega138k_ae350'
        fixture=ROOT/'build/host/ae350-hdmi/tty-expected.rgb565'
        fixture.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='ae350-video-hal-') as directory:
            directory=Path(directory)
            (directory/'ae350_address_map.h').write_text(AddressMap(resolve_machine('mega138k_ae350',ROOT)).c_header())
            executable=directory/'test'
            command=[cc,'-std=c11','-O2','-Wall','-Wextra','-Werror',
                '-DAE350_VIDEO_HOST_TEST','-DVT_COLS=80','-DVT_ROWS=30',
                '-I'+str(directory),'-I'+str(bsp/'video'),'-I'+str(bsp/'dma'),
                '-I'+str(ROOT/'firmware/bios'),'-I'+str(ROOT/'firmware/common'),
                '-I'+str(ROOT/'firmware/examples/mega138k_ae350'),
                str(bsp/'video/ae350_video.c'),str(bsp/'video/ae350_tty.c'),
                str(ROOT/'firmware/bios/vt.c'),str(Path(__file__).with_suffix('.c')),
                '-o',str(executable)]
            subprocess.run(command,check=True)
            result=subprocess.run([str(executable),str(fixture)],text=True,capture_output=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('PASS video HAL',result.stdout)
            self.assertEqual(fixture.stat().st_size,640*480*2)


if __name__=='__main__':unittest.main()
