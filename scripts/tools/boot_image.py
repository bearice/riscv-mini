"""Base image ABI framing; explicit little-endian header shared with image.h.

Machine-neutral: every platform reuses this framing and the CRC rules.  The
20K image ABI *tag* (``abi_tag``) is a platform semantic and stays in
``scripts/primer20k_vexriscv/boot_image.py``.
"""
import struct
import zlib

MAGIC=0x354d5652
VERSION=1
HEADER=struct.Struct('<12I')
LOAD=0x00800000
FLASH_OFFSET=0x200000
FLASH_SIZE=0x400000
MAX_PAYLOAD=FLASH_SIZE-FLASH_OFFSET-HEADER.size
CHUNK=128


def pack_image(payload,abi,load=LOAD):
    """Wrap a payload with the shared 12-word header.

    ``load`` lets a platform with a different boot address (AE350 DDR at 0x3000)
    reuse the same framing, CRC rules and upload protocol."""
    if not 4<=len(payload)<=MAX_PAYLOAD: raise ValueError('Invalid payload size')
    words=[MAGIC,VERSION,HEADER.size,abi,len(payload),load,load,zlib.crc32(payload),0,0,0,0]
    words[-1]=zlib.crc32(HEADER.pack(*words)[:-4])
    return HEADER.pack(*words)+payload


def unpack_image(image,abi=None,load=None):
    if len(image)<HEADER.size: raise ValueError('Truncated header')
    w=HEADER.unpack_from(image)
    if w[0:3]!=(MAGIC,VERSION,HEADER.size) or w[8:11]!=(0,0,0): raise ValueError('Invalid format')
    if zlib.crc32(image[:HEADER.size-4])!=w[-1]: raise ValueError('Header CRC')
    if abi is not None and w[3]!=abi: raise ValueError('Incompatible ABI')
    base=LOAD if load is None else load
    if not 4<=w[4]<=MAX_PAYLOAD or w[5]!=base or w[6]&3 or not base<=w[6]<=base+w[4]-4:
        raise ValueError('Invalid load range or entry')
    if len(image)!=HEADER.size+w[4]: raise ValueError('Length mismatch')
    payload=image[HEADER.size:]
    if zlib.crc32(payload)!=w[7]: raise ValueError('Payload CRC')
    return w,payload


def packet(sequence,data):
    if not 0<len(data)<=CHUNK: raise ValueError('Invalid packet size')
    body=struct.pack('<IH',sequence,len(data))+data
    return body+struct.pack('<I',zlib.crc32(body))
