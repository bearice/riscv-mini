"""The user-confirmed pin map must be exclusive and enter the image ABI."""
import sys
import copy
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from gateware.boards.mega138k_pro import io_map
from gateware.machines.mega138k_ae350 import address_map as amap

b=io_map.load();binding=io_map.audio_binding()
assert [binding['rx'][k] for k in ('data','bck','lr','ws')]==['N18','N26','R16','R17']
assert amap.interface()['audio']['tx']['selector']==9
assert amap.interface()['audio']['rx']['selector']==10
for edit in ('owner','collision','voltage'):
    bad=copy.deepcopy(b)
    r=bad['resources']['microphone']
    if edit=='owner': r['active_owner']='SSPI'
    elif edit=='collision': r['data']=bad['resources']['audio']['data']
    else: r['bank_vccio']=1.8
    with patch.object(io_map,'load',return_value=bad):
        try:io_map.audio_binding()
        except ValueError:pass
        else:raise AssertionError(edit+' accepted')
old=amap.image_abi()
with patch.object(amap,'AUDIO_DMA_SEL',8): assert amap.image_abi()!=old
print('PASS microphone wiring, exclusive ownership, electrical collision rejection, DMA route image ABI')
