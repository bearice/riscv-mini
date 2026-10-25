"""Connectivity requires chip identity/data, idle lines and explicit NACKs."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from mini_ops.mega138k_ae350.ae350_bus_contract import judge,DONE,SCHEMA,NACK,INVALID
w=[0]*48
w[0]=DONE;w[31]=SCHEMA;w[2]=0x02020001;w[3]=0xef4018;w[4]=1<<24;w[6]=0x50444653
w[7]=w[8]=123;w[11]=0x02021001;w[12]=0x6001;w[13]=2;w[16]=w[17]=456
w[41]=255
w[18]=NACK;w[21]=2<<20;w[23]=0x6001;w[25]=w[26]=INVALID;w[27]=1000000
assert not judge(w,0x3100,0x3000,0x10000,[0]*5)
for index in (0,1,3,4,6,8,9,10,11,12,13,14,15,17,18,21,22,23,24,25,26,27,29,31,41,42):
 bad=w.copy();bad[index]^=1
 # Capacity variation by one remains valid; force zero instead.
 if index==4:bad[index]=0
 if index==11:bad[index]^=1<<12
 if index in (12,23):bad[index]^=1<<13
 assert judge(bad,0x3100,0x3000,0x10000,[0]*5),index
print('PASS management result rejects missing identification, false completion, bad data/lines/NACK/cleanup')
