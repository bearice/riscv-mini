"""Host checks for the video DT ABI and board-test memory dump parser."""
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from scripts.opensbi_build import lcd_node
from scripts.uboot_verify import read_words

class VideoTest(unittest.TestCase):
    def test_no_video(self):
        self.assertEqual(lcd_node({'csr_bases':{}}),'')

    def test_actual_offsets_and_pre_relocation(self):
        names=('enable','select','base0','base1','address_error','state')
        csr={'csr_bases':{'rgb_lcd':0xf0007800},'csr_registers':{
            'rgb_lcd_'+n:{'addr':0xf0007800+4*i,'size':1} for i,n in enumerate(names)}}
        node=lcd_node(csr)
        self.assertIn('reg=<0xf0007800 0x18>',node)
        self.assertIn('bootph-all',node)
        self.assertIn('csr-offsets=<0x0 0x4 0x8 0xc 0x14 0x10>',node)
        del csr['csr_registers']['rgb_lcd_base0']
        with self.assertRaises(ValueError):lcd_node(csr)

    def test_memory_rows(self):
        reply=b'md.l 7fc0400 5\r\n07fc0400: 00000001 ffffffff 0000ffff 12345678    ....\r\n07fc0410: aabbccdd    ....\r\nriscv-mini> '
        self.assertEqual(read_words(reply,0x7fc0400,5),[1,0xffffffff,0xffff,0x12345678,0xaabbccdd])
        with self.assertRaises(AssertionError):read_words(reply,0x7fc0404,5)

if __name__=='__main__':unittest.main()
