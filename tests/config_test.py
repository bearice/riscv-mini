"""Configuration rejects incompatible RTL, dependency and PLL selections."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gateware.features import Features
from gateware.config import storage_profile, cpu_configuration, pll_count, cpu_isa, cpu_capabilities
import tempfile, hashlib, json

def rejected(function,*args):
    try:function(*args)
    except ValueError:return
    raise AssertionError('Invalid configuration accepted')

full=Features.resolve('full')
assert full.mmu and full.fpu
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
assert cpu_isa(full,{'compressed':True,'bitmanip':['Zba','Zbb','Zbs']})=='rv32imafc_zba_zbb_zbs_zicsr_zifencei'
assert cpu_isa(full,{'compressed':True,'bitmanip':[]})=='rv32imafc_zicsr_zifencei'
from scripts.boot_image import abi_tag
base={'csr_registers':{},'memories':{},'constants':{}}
small={**base,'constants':{'config_l2_size':4096}}
large={**base,'constants':{'config_l2_size':8192}}
assert len({abi_tag(base),abi_tag(small),abi_tag(large)})==3
coherent={**base,'constants':{'config_l2_size':4096,'config_l2_mode':2,'config_shared_l2':1}}
assert abi_tag(coherent)!=abi_tag(small),'Memory coherence policy must change the image ABI'
with tempfile.TemporaryDirectory() as directory:
    cpu=Path(directory)/'TestCPU.v';cpu.write_text('MmuPlugin FpuPlugin module DataCache')
    record={'sha256':hashlib.sha256(cpu.read_bytes()).hexdigest(),'compressed':True,'bitmanip':['Zba','Zbb','Zbs']}
    (cpu.parent/'generator.json').write_text(json.dumps({'cpu_rtls':{'TestCPU':record}}))
    assert cpu_capabilities(cpu)['compressed']
    cpu.write_text(cpu.read_text()+' changed')
    rejected(cpu_capabilities,cpu)
print('Configuration validation PASS')
