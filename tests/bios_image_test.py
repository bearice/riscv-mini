"""Payload layout rejection tests for the actual pack/unpack interface."""
import sys,unittest,struct,zlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import bios_image as image
class ImageTest(unittest.TestCase):
    def test_roundtrip(self):
        data=image.pack(bytes(range(256))*3,4096)
        h,p=image.unpack(data);self.assertEqual(h[4],4096);self.assertEqual(len(p),768)
    def test_invalid_layout(self):
        for payload,memory,entry in [(b'x',4,image.LOAD),(b'abcd',3,image.LOAD),
                (b'abcd',image.LIMIT-image.LOAD,image.LOAD),(b'abcd',4,image.LOAD+1),
                (b'abcd',4,image.LOAD+4)]:
            with self.subTest(memory=memory,entry=entry),self.assertRaises(ValueError):image.pack(payload,memory,entry)
    def test_corruption_and_length(self):
        valid=image.pack(b'abcd'*200,8192)
        for n in [0,27,31,32,len(valid)-1]:
            with self.subTest(length=n),self.assertRaises(ValueError):image.unpack(valid[:n])
        for index in [0,4,8,12,16,20,24,28,32,len(valid)-1]:
            bad=bytearray(valid);bad[index]^=1
            with self.subTest(index=index),self.assertRaises(ValueError):image.unpack(bad)
        with self.assertRaises(ValueError):image.unpack(valid+b'x')
    def test_crc_does_not_authorize_reserved_memory(self):
        data=image.pack(b'abcd')
        w=list(image.HEADER.unpack_from(data));w[2]=0x40800000;w[-1]=zlib.crc32(struct.pack('<7I',*w[:-1]))
        with self.assertRaises(ValueError):image.unpack(image.HEADER.pack(*w)+data[32:])
if __name__=='__main__':unittest.main()
