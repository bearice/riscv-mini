"""Base image ABI; explicit little-endian framing shared with image.h."""
import json
import struct
import zlib

MAGIC=0x354d5652
VERSION=1
HEADER=struct.Struct('<12I')
LOAD=0x40800000
FLASH_OFFSET=0x200000
FLASH_SIZE=0x400000
MAX_PAYLOAD=FLASH_SIZE-FLASH_OFFSET-HEADER.size
CHUNK=128

def abi_tag(csr):
    interface={'registers':csr['csr_registers'],'memories':csr['memories'],
               'interrupts':{k:v for k,v in csr.get('constants',{}).items() if k.lower().endswith('_interrupt')},
               'cpu':'vexriscv-lite-rv32im','sys_hz':60000000,'load':LOAD,'image_version':VERSION}
    features={k:v for k,v in csr.get('constants',{}).items() if k.lower().startswith('mini_feature_')}
    if features:interface['features']=features
    if csr.get('constants',{}).get('config_usb_ultra'):interface['usb_backend']='ultra'
    extensions={name:csr.get('constants',{}).get('config_cpu_'+name,0) for name in ('compressed','bitmanip')}
    if any(extensions.values()):interface['cpu_extensions']=extensions
    l2_size=csr.get('constants',{}).get('config_l2_size',0)
    if l2_size:interface['l2']={'size':l2_size,'policy':'write-through/write-invalidate'}
    return zlib.crc32(json.dumps(interface,sort_keys=True,separators=(',',':')).encode())

def pack_image(payload,abi):
    if not 4<=len(payload)<=MAX_PAYLOAD: raise ValueError('Invalid payload size')
    words=[MAGIC,VERSION,HEADER.size,abi,len(payload),LOAD,LOAD,zlib.crc32(payload),0,0,0,0]
    words[-1]=zlib.crc32(HEADER.pack(*words)[:-4])
    return HEADER.pack(*words)+payload

def unpack_image(image,abi=None):
    if len(image)<HEADER.size: raise ValueError('Truncated header')
    w=HEADER.unpack_from(image)
    if w[0:3]!=(MAGIC,VERSION,HEADER.size) or w[8:11]!=(0,0,0): raise ValueError('Invalid format')
    if zlib.crc32(image[:HEADER.size-4])!=w[-1]: raise ValueError('Header CRC')
    if abi is not None and w[3]!=abi: raise ValueError('Incompatible ABI')
    if not 4<=w[4]<=MAX_PAYLOAD or w[5]!=LOAD or w[6]&3 or not LOAD<=w[6]<=LOAD+w[4]-4:
        raise ValueError('Invalid load range or entry')
    if len(image)!=HEADER.size+w[4]: raise ValueError('Length mismatch')
    payload=image[HEADER.size:]
    if zlib.crc32(payload)!=w[7]: raise ValueError('Payload CRC')
    return w,payload

def packet(sequence,data):
    if not 0<len(data)<=CHUNK: raise ValueError('Invalid packet size')
    body=struct.pack('<IH',sequence,len(data))+data
    return body+struct.pack('<I',zlib.crc32(body))
