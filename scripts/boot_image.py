"""Base image ABI; explicit little-endian framing shared with image.h."""
import json
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

def abi_tag(csr):
    registers=dict(csr['csr_registers'])
    # The LCD ring is a backwards-compatible, reset-disabled extension. Keep
    # the existing XIP loader usable for RAM experiments without rewriting
    # Flash. Only the exact appended layout is compatible; relocated or
    # malformed registers remain part of the ABI hash.
    lcd_base=csr.get('csr_bases',{}).get('rgb_lcd')
    if lcd_base is not None and all(registers.get(name)==
            {'addr':lcd_base+offset,'size':1,'type':'rw'}
            for name,offset in [('rgb_lcd_wrap_start',0x24),('rgb_lcd_wrap_end',0x28)]):
        del registers['rgb_lcd_wrap_start']
        del registers['rgb_lcd_wrap_end']
    interface={'registers':registers,'memories':csr['memories'],
               'interrupts':{k:v for k,v in csr.get('constants',{}).items() if k.lower().endswith('_interrupt')},
               'cpu':'vexriscv-lite-rv32im','sys_hz':60000000,'load':LOAD,'image_version':VERSION}
    features={k:v for k,v in csr.get('constants',{}).items() if k.lower().startswith('mini_feature_')}
    if features:interface['features']=features
    if csr.get('constants',{}).get('config_usb_ultra'):interface['usb_backend']='ultra'
    extensions={name:csr.get('constants',{}).get('config_cpu_'+name,0) for name in ('compressed','bitmanip')}
    if any(extensions.values()):interface['cpu_extensions']=extensions
    l2_size=csr.get('constants',{}).get('config_l2_size',0)
    if l2_size:
        constants=csr.get('constants',{})
        interface['l2']={'size':l2_size,
            'policy':'write-back' if constants.get('config_l2_mode')==2 else 'write-through/write-invalidate',
            'io_coherent':bool(constants.get('config_shared_l2'))}
    return zlib.crc32(json.dumps(interface,sort_keys=True,separators=(',',':')).encode())

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
