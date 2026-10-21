"""Configuration rejects incompatible RTL, dependency and PLL selections."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from gateware.machines.primer20k_vexriscv.features import Features
from gateware.machines.primer20k_vexriscv.config import storage_profile, cpu_configuration, pll_count, cpu_isa, cpu_capabilities
import tempfile, hashlib, json

def rejected(function,*args):
    try:function(*args)
    except ValueError:return
    raise AssertionError('Invalid configuration accepted')

full=Features.resolve('full')
assert full.mmu and not full.fpu
assert not Features().fpu
assert Features.resolve('full', {'fpu': True}).fpu
assert full.eth_dma
assert Features.resolve('full',{'eth_dma':True}).eth_dma
rejected(Features.resolve,'full',{'eth_dma':True,'eth':False})
assert not Features.resolve('minimal').mmu and not Features.resolve('minimal').fpu
assert not Features.resolve('full',{'mmu':False,'fpu':False}).mmu
assert storage_profile(None,None,full)[0]=='lite'
assert storage_profile('none',None,full)[1].filesystem is False
assert storage_profile('full',None,Features.resolve('minimal'))[1].sd
rejected(storage_profile,'spi','native',full)
rejected(storage_profile,'none',None,full,{'filesystem':True})
rejected(cpu_configuration,Features.resolve('full',{'fpu':True}),{},None)
assert pll_count(full,'ultra','dds')==3
assert pll_count(full,'ohci','dds')==4
assert pll_count(Features.resolve('minimal'),'ultra','dds')==1
assert cpu_isa(Features.resolve('full', {'fpu': True}),{'compressed':True,'bitmanip':['Zba','Zbb','Zbs']})=='rv32imafc_zba_zbb_zbs_zicsr_zifencei'
assert cpu_isa(Features.resolve('full', {'fpu': True}),{'compressed':True,'bitmanip':[]})=='rv32imafc_zicsr_zifencei'
from boot_image import abi_tag
base={'csr_registers':{},'memories':{},'constants':{}}
small={**base,'constants':{'config_l2_size':4096}}
large={**base,'constants':{'config_l2_size':8192}}
assert len({abi_tag(base),abi_tag(small),abi_tag(large)})==3
larger={**base,'constants':{'config_l2_size':16384}}
assert abi_tag(larger) not in {abi_tag(base),abi_tag(small),abi_tag(large)}
coherent={**base,'constants':{'config_l2_size':4096,'config_l2_mode':2,'config_shared_l2':1}}
assert abi_tag(coherent)!=abi_tag(small),'Memory coherence policy must change the image ABI'
lcd={**base,'csr_bases':{'rgb_lcd':0xf0008000},
     'csr_registers':{'rgb_lcd_enable':{'addr':0xf0008000,'size':1,'type':'rw'}}}
ring={**lcd,'csr_registers':{**lcd['csr_registers'],
      'rgb_lcd_wrap_start':{'addr':0xf0008024,'size':1,'type':'rw'},
      'rgb_lcd_wrap_end':{'addr':0xf0008028,'size':1,'type':'rw'}}}
assert abi_tag(lcd)==abi_tag(ring),'Reset-disabled appended ring must preserve XIP ABI'
ring['csr_registers']['rgb_lcd_wrap_end']['addr']+=4
assert abi_tag(lcd)!=abi_tag(ring),'Relocated ring is not the compatible extension'
with tempfile.TemporaryDirectory() as directory:
    cpu=Path(directory)/'TestCPU.v';cpu.write_text('MmuPlugin FpuPlugin module DataCache')
    record={'sha256':hashlib.sha256(cpu.read_bytes()).hexdigest(),'compressed':True,'bitmanip':['Zba','Zbb','Zbs']}
    (cpu.parent/'generator.json').write_text(json.dumps({'cpu_rtls':{'TestCPU':record}}))
    assert cpu_capabilities(cpu)['compressed']
    cpu.write_text(cpu.read_text()+' changed')
    rejected(cpu_capabilities,cpu)
print('Configuration validation PASS')
