"""Incomplete read-only bring-up must not be confused with host verification."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from mini_ops.mega138k_ae350.ae350_sd_contract import DONE,judge
w=[0]*32
w[0]=DONE;w[31]=0x53440001;w[3]=w[6]=0x12345678
w[19]=1;w[20]=7
w[8:14]=[0xa5,0xa5,100000,25000000,4,9]
assert not judge(w,0x3100,0,0x3000,0x10000,[0]*5)
for index in (0,1,2,5,6,8,9,11,12,13,14,17,18,19,21,31):
    bad=w.copy();bad[index]^=1
    assert judge(bad,0x3100,0,0x3000,0x10000,[0]*5),index
bad=w.copy();bad[10]=0
assert judge(bad,0x3100,0,0x3000,0x10000,[0]*5)
assert judge(w,0x3100,0,0x3000,0x10000,[1,0,0,0,0])
print('PASS SD read contract rejects errors, partial completion, CRC mismatch and guard corruption')
