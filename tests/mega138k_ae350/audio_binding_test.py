"""The user-confirmed pin map must be exclusive and enter the image ABI."""
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from gateware.description import resolve_machine
from gateware.io import IOBindings
io_map = IOBindings(resolve_machine('mega138k_ae350'))
from gateware.machines.mega138k_ae350.address_map import AddressMap
amap = AddressMap(io_map.machine)

binding=io_map.audio_binding()
assert [binding['rx'][k] for k in ('data','bck','lr','ws')]==['N18','N26','R16','R17']
assert amap.interface()['audio']['tx']['selector']==9
assert amap.interface()['audio']['rx']['selector']==10
from gateware.description import load_machine, validate_pair
import copy
board, machine = load_machine('mega138k_ae350')
for edit in ('collision', 'voltage'):
    bad = copy.deepcopy(board)
    data = bad['resources']['microphone']['signals']['data']
    if edit == 'collision': data['pins'] = board['resources']['audio']['signals']['data']['pins']
    else: data['bank_vccio'] = 1.8
    try: validate_pair(bad, machine)
    except ValueError: pass
    else: raise AssertionError(edit + ' accepted')
old=amap.image_abi()
with patch.object(amap,'AUDIO_DMA_SEL',8): assert amap.image_abi()!=old
print('PASS microphone wiring, exclusive ownership, electrical collision rejection, DMA route image ABI')
