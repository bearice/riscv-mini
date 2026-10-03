"""Configuration dependencies and boot ABI compatibility, without programming hardware."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gateware.features import Features
from scripts.boot_image import abi_tag,pack_image,unpack_image

assert all(Features.resolve().as_dict().values())
minimal=Features.resolve('minimal')
assert [k for k,v in minimal.as_dict().items() if v]==['flash']
assert not any(Features.resolve('minimal',{'flash':False}).as_dict().values())
assert not Features.resolve(overrides={'sd':False}).filesystem
assert not Features.resolve(overrides={'mic':False}).mic_stereo
assert not Features.resolve(overrides={'flash':False}).eth
for child,parent in [('filesystem','sd'),('mic_stereo','mic'),('eth','flash')]:
    try:Features.resolve(overrides={child:True,parent:False})
    except ValueError:pass
    else:raise AssertionError('Explicit incompatible flags must fail')
full={'csr_registers':{},'memories':{},'constants':{'mini_feature_filesystem':1}}
raw={'csr_registers':{},'memories':{},'constants':{'mini_feature_filesystem':0}}
assert abi_tag(full)!=abi_tag(raw)
compressed={**full,'constants':{**full['constants'],'config_cpu_compressed':1}}
bitmanip={**compressed,'constants':{**compressed['constants'],'config_cpu_bitmanip':7}}
assert len({abi_tag(full),abi_tag(compressed),abi_tag(bitmanip)})==3
image=pack_image(b'1234',abi_tag(full))
try:unpack_image(image,abi_tag(raw))
except ValueError:pass
else:raise AssertionError('An image for another feature configuration must be rejected')
print('Feature dependencies and incompatible image rejection PASS')
