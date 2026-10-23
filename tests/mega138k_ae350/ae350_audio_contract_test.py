"""Digital result must reject partial completion, loss and invented physical pass."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from mini_ops.mega138k_ae350.ae350_audio_contract import NAMES,DONE,SCHEMA,judge,judge_routes,judge_video

d=dict.fromkeys(NAMES,0)
d.update(stage=DONE,schema=SCHEMA,pio=256,pio_gaps=64,restarts=100,snapshot=512,
         overflow=512,busy_reject=11,hw_error=12,frames=240000,played=240000,
         tx_blocks=156,rx_blocks=156,cpu_writes=100000,sample_rate=48000,seconds=5,cache_control=2,accepted=240000,dma_restarts=100)
words=lambda row:[row[k] for k in NAMES]
assert judge(words(d),5)==[]
assert judge(words(d),1800)
for key in ('stage','schema','pio','restarts','snapshot','overflow','busy_reject','hw_error',
            'frames','played','tx_blocks','rx_blocks','cpu_writes','sample_rate','seconds',
            'accepted','cache_control','dma_restarts'):
    bad={**d,key:0};assert judge(words(bad),5),key
for key in ('error','underruns','rx_overruns','rx_bad_sign','guards','physical'):
    bad={**d,key:1};assert judge(words(bad),5),key
route=words(d);route[1]=34
route[27:31]=[0x240,0x40,0x440,0x40]
assert judge_routes(route,[0x41,0x40,0x42,0x40])==[]
assert judge_routes(route,[0x41,0x40,0x43,0x40]), 'ambiguous RX route must fail'
print('PASS audio result rejects incomplete duration, failures, losses and invented acoustic pass')

video=[0]*32
video[0]=0x56490002;video[2:5]=[640,480,3600]
video[12]=60*59*19200;video[13]=100000
video[16:19]=[0x01000000,1,0]
assert judge_video(video,60)==[]
for index in (0,2,3,4,12,13,16,17):
    bad=video.copy();bad[index]=0;assert judge_video(bad,60),index
for index in (1,5,6,7,8,9,14,15,18):
    bad=video.copy();bad[index]=1;assert judge_video(bad,60),index
bad=video.copy();bad[12]=19200
assert judge_video(bad,60),'one fetched frame cannot pass a continuous scanout soak'
print('PASS video stress requires continuous reads, active output and zero errors/guards')
