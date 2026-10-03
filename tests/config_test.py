"""Configuration rejects incompatible RTL, dependency and PLL selections."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gateware.features import Features
from gateware.config import storage_profile, cpu_configuration, pll_count

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
print('Configuration validation PASS')
