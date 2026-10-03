"""Portable resident-BIOS payload image, distinct from the ROM firmware image."""
import struct,zlib
HEADER=struct.Struct('<8I')
MAGIC=0x31425052
OS_MAGIC=0x3142534f
VERSION=1
LOAD=0x41000000
LIMIT=0x47e00000
MAX_FILE=4*1024*1024
def pack(payload,memory_bytes=None,entry=LOAD,*,os_image=False):
    n=len(payload);memory_bytes=n if memory_bytes is None else memory_bytes
    if not 4<=n<=MAX_FILE or not n<=memory_bytes<=LIMIT-LOAD-65536 or entry&3 or not LOAD<=entry<=LOAD+n-4:
        raise ValueError('Invalid payload layout')
    words=[OS_MAGIC if os_image else MAGIC,VERSION,LOAD,n,memory_bytes,entry,zlib.crc32(payload),0]
    words[-1]=zlib.crc32(HEADER.pack(*words)[:-4])
    return HEADER.pack(*words)+payload
def unpack(data):
    if len(data)<HEADER.size:raise ValueError('Truncated header')
    w=HEADER.unpack_from(data)
    if w[0] not in (MAGIC,OS_MAGIC) or w[1:3]!=(VERSION,LOAD) or zlib.crc32(data[:28])!=w[-1]:raise ValueError('Header')
    payload=data[32:]
    if pack(payload,w[4],w[5],os_image=w[0]==OS_MAGIC)!=data:raise ValueError('Layout/length/CRC')
    return w,payload
