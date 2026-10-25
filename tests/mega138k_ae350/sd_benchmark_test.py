"""Throughput parser must reject partial runs and changed read contents."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from mini_ops.mega138k_ae350.ae350_sd_benchmark import parse
lines=[('SD_BENCH CONFIG 00989680 00000004 2faf0800 00000002',0)]
for i in range(9):
    lines += [(f'SD_BENCH START {i:08x}',i*3+1),
              (f'SD_BENCH END {i:08x} 5f5e1000 00000000',i*3+3),
              (f'SD_BENCH CHECK {i:08x} 12345678 00000000 00000001',i*3+3.1)]
c,rows=parse(lines)
assert len(rows)==9 and rows[0]['mib_s']==.5 and rows[0]['observed_cycle_hz']==800000000
for bad in (lines[:-1],lines[:-2],lines[:-3],[(x.replace('12345678','12345679') if n==3 else x,t) for n,(x,t) in enumerate(lines)]):
    try:parse(bad)
    except (ValueError,KeyError):pass
    else:raise AssertionError('bad benchmark accepted')
print('PASS SD benchmark parsing, independent host timing and incomplete/content mismatch rejection')
