"""Image/transport checks, including headers whose CRC was recomputed after tampering."""
import struct
import sys
import unittest
import zlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from boot_image import HEADER, LOAD, MAX_PAYLOAD, pack_image, unpack_image, packet

class ImageTests(unittest.TestCase):
    def setUp(self):
        self.image=pack_image(bytes(range(256))*3,0x12345678)
    def test_roundtrip_and_corruption(self):
        self.assertEqual(unpack_image(self.image,0x12345678)[1],bytes(range(256))*3)
        for i in (0,HEADER.size-1,HEADER.size,len(self.image)-1):
            changed=bytearray(self.image);changed[i]^=1
            with self.assertRaises(ValueError): unpack_image(changed,0x12345678)
    def test_range_abi_flags_with_valid_header_crc(self):
        for field,value in ((3,0),(4,0),(4,0xffffffff),(4,MAX_PAYLOAD+1),(5,0),
                (5,0x47e00000),(6,LOAD+1),(6,LOAD+768),(8,1),(9,1),(10,1)):
            words=list(HEADER.unpack_from(self.image));words[field]=value
            words[-1]=zlib.crc32(HEADER.pack(*words)[:-4])
            with self.assertRaises(ValueError): unpack_image(HEADER.pack(*words)+self.image[HEADER.size:],0x12345678)
    def test_truncation_and_trailing_data(self):
        for data in (self.image[:10],self.image[:-1],self.image+b'x'):
            with self.assertRaises(ValueError): unpack_image(data)
    def test_transport_crc_and_sequence(self):
        data=b'hello';framed=packet(0x10203040,data)
        self.assertEqual(struct.unpack_from('<IH',framed),(0x10203040,5))
        self.assertEqual(struct.unpack('<I',framed[-4:])[0],zlib.crc32(framed[:-4]))
        self.assertEqual(framed[6:-4],data)

if __name__=='__main__': unittest.main()
