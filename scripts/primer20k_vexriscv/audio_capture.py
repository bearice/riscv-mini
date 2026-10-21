"""Windows line-in capture with WinMM; no audio driver or third-party dependency."""
import argparse
import array
import ctypes as ct
import json
import math
import sys
import time
import wave
from pathlib import Path
from ctypes import wintypes as wt

class Caps(ct.Structure):
    _fields_=[('mid',wt.WORD),('pid',wt.WORD),('version',wt.DWORD),('name',wt.WCHAR*32),
              ('formats',wt.DWORD),('channels',wt.WORD),('reserved',wt.WORD)]
class Format(ct.Structure):
    _pack_=1
    _fields_=[('tag',wt.WORD),('channels',wt.WORD),('rate',wt.DWORD),('byte_rate',wt.DWORD),
              ('block',wt.WORD),('bits',wt.WORD),('extra',wt.WORD)]
class Header(ct.Structure):
    _fields_=[('data',ct.c_void_p),('length',wt.DWORD),('recorded',wt.DWORD),('user',ct.c_size_t),
              ('flags',wt.DWORD),('loops',wt.DWORD),('next',ct.c_void_p),('reserved',ct.c_size_t)]

def api():
    if sys.platform!='win32':raise RuntimeError('WinMM capture requires Windows')
    lib=ct.WinDLL('winmm')
    lib.waveInGetNumDevs.restype=wt.UINT
    lib.waveInGetDevCapsW.argtypes=[ct.c_size_t,ct.POINTER(Caps),wt.UINT]
    lib.waveInOpen.argtypes=[ct.POINTER(ct.c_void_p),wt.UINT,ct.POINTER(Format),ct.c_size_t,ct.c_size_t,wt.DWORD]
    for name in ('waveInPrepareHeader','waveInUnprepareHeader','waveInAddBuffer'):
        getattr(lib,name).argtypes=[ct.c_void_p,ct.POINTER(Header),wt.UINT]
    for name in ('waveInStart','waveInStop','waveInReset','waveInClose'):
        getattr(lib,name).argtypes=[ct.c_void_p]
    return lib

def devices():
    lib=api();result=[]
    for i in range(lib.waveInGetNumDevs()):
        caps=Caps();check(lib.waveInGetDevCapsW(i,ct.byref(caps),ct.sizeof(caps)))
        result.append({'id':i,'name':caps.name,'channels':caps.channels})
    return result

def check(code):
    if code:raise RuntimeError(f'WinMM error {code}')

def record(output,seconds=4,device_name='Line In',on_start=None):
    candidates=[d for d in devices() if device_name.lower() in d['name'].lower()]
    if len(candidates)!=1:raise RuntimeError(f'Choose exactly one input: {candidates}')
    device=candidates[0];lib=api();handle=ct.c_void_p();rate=48000
    fmt=Format(1,2,rate,rate*4,4,16,0)
    check(lib.waveInOpen(ct.byref(handle),device['id'],ct.byref(fmt),0,0,0))
    buffer=ct.create_string_buffer(round(seconds*rate)*4)
    header=Header(ct.cast(buffer,ct.c_void_p),len(buffer),0,0,0,0,None,0);prepared=False
    try:
        check(lib.waveInPrepareHeader(handle,ct.byref(header),ct.sizeof(header)));prepared=True
        check(lib.waveInAddBuffer(handle,ct.byref(header),ct.sizeof(header)))
        check(lib.waveInStart(handle))
        if on_start:on_start()
        deadline=time.monotonic()+seconds+3
        while not header.flags&1:
            if time.monotonic()>deadline:raise TimeoutError('Line-in buffer did not complete')
            time.sleep(.025)
        data=buffer.raw[:header.recorded]
    finally:
        lib.waveInStop(handle);lib.waveInReset(handle)
        if prepared:check(lib.waveInUnprepareHeader(handle,ct.byref(header),ct.sizeof(header)))
        check(lib.waveInClose(handle))
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(output),'wb') as wav:
        wav.setnchannels(2);wav.setsampwidth(2);wav.setframerate(rate);wav.writeframes(data)
    return device

def tone_amplitude(samples,rate,hz):
    # Exact-frequency projection, normalized to signed 16-bit full scale.
    real=imag=0.0;mean=sum(samples)/len(samples)
    for i,value in enumerate(samples):
        angle=2*math.pi*hz*i/rate
        real+=(value-mean)*math.cos(angle);imag+=(value-mean)*math.sin(angle)
    return 2*math.hypot(real,imag)/(len(samples)*32768)

def analyze(path,source_rate=46875):
    with wave.open(str(path),'rb') as wav:
        rate=wav.getframerate();assert wav.getnchannels()==2 and wav.getsampwidth()==2
        data=array.array('h',wav.readframes(wav.getnframes()))
    channels=[data[i::2] for i in (0,1)];result=[]
    for channel,expected,other in zip(channels,(source_rate/64,source_rate/96),(source_rate/96,source_rate/64)):
        active=channel[round(.25*rate):round(1.5*rate)];silent=channel[round(2.5*rate):round(3.5*rate)]
        # Coarse peak and a local refinement tolerate board/host oscillator ppm.
        peaks=[(tone_amplitude(active,rate,expected+delta),expected+delta) for delta in range(-8,9)]
        _,coarse=max(peaks)
        amplitude,hz=max((tone_amplitude(active,rate,coarse+delta/10),coarse+delta/10) for delta in range(-10,11))
        cross=tone_amplitude(active,rate,other)
        rms=math.sqrt(sum(x*x for x in active)/len(active))/32768
        quiet=math.sqrt(sum(x*x for x in silent)/len(silent))/32768
        dc=sum(active)/len(active);quiet_dc=sum(silent)/len(silent)
        quiet_tone=tone_amplitude(silent,rate,hz)
        result.append({'expected_hz':expected,'peak_hz':hz,'tone_amplitude':amplitude,'rms':rms,
                       'silent_rms':quiet,'dc':dc/32768,'silent_dc':quiet_dc/32768,
                       'ac_rms':math.sqrt(sum((x-dc)**2 for x in active)/len(active))/32768,
                       'silent_ac_rms':math.sqrt(sum((x-quiet_dc)**2 for x in silent)/len(silent))/32768,
                       'mute_tone_attenuation_db':20*math.log10(max(amplitude,1e-10)/max(quiet_tone,1e-10)),
                       'separation_db':20*math.log10(max(amplitude,1e-10)/max(cross,1e-10)),
                       'clipped_samples':sum(abs(x)>=32760 for x in active)})
    return {'rate':rate,'channels':result}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list',action='store_true');parser.add_argument('--device',default='Line In')
    parser.add_argument('--output',type=Path,default=Path('build/m7-line-in.wav'))
    parser.add_argument('--analyze',type=Path)
    parser.add_argument('--source-rate',type=int,default=46875,help='FPGA audio frame rate (48000 in PLL mode)')
    args=parser.parse_args()
    if args.list:print(json.dumps(devices(),indent=2,ensure_ascii=False))
    elif args.analyze:print(json.dumps(analyze(args.analyze,args.source_rate),indent=2))
    else:print(record(args.output,device_name=args.device))
