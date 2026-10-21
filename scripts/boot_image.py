"""20K image ABI tag.  Framing itself is machine-neutral: tools/boot_image.py."""
import json
import zlib
from tools.boot_image import LOAD, VERSION


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
