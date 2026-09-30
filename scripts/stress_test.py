"""M4 board soak: existing SD test file is read only; no programming/Flash writes."""
import argparse
from contextlib import contextmanager
import ctypes
import hashlib
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
import serial

ROOT=Path(__file__).resolve().parents[1]
STATUS=re.compile(r'active=([0-9a-f]+) completed=([0-9a-f]+) checksum=([0-9a-f]+) busy=([0-9a-f]+) frames=([0-9a-f]+) underflows=([0-9a-f]+)')
CHECKSUM=(0x80d32504,0x82c2e704)

@contextmanager
def keep_host_awake():
    """Hold a reversible system-sleep request on this thread for the board test."""
    if os.name!='nt':
        yield False
        return
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    set_state=kernel.SetThreadExecutionState
    set_state.argtypes=[ctypes.c_uint]
    set_state.restype=ctypes.c_uint
    continuous=0x80000000
    if not set_state(continuous|0x00000001):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield True
    finally:
        set_state(continuous)

def parse_status(text):
    match=STATUS.search(text)
    if not match: raise RuntimeError('Missing framebuffer status')
    status=dict(zip(('active','completed','checksum','busy','frames','underflows'),(int(x,16) for x in match.groups())))
    if status['active'] not in (0,1) or status['underflows'] or status['checksum']!=CHECKSUM[status['active']]:
        raise RuntimeError(f'Invalid frame data/status: {status}')
    return status

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',default='COM4')
    parser.add_argument('--seconds',type=float,default=300)
    parser.add_argument('--name',default='stress')
    args=parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds<=0: parser.error('Duration must be positive and finite')
    if not re.fullmatch(r'[a-zA-Z0-9_-]+',args.name): parser.error('Use a simple output name')
    output=ROOT/'build/m4'
    validation=json.loads((output/'validation.json').read_text())
    hardware=json.loads((output/'hardware-validation.json').read_text())
    fs=output/'gateware/riscv_mini.fs'
    if not hardware['acceptance_passed'] or hardware['bitstream_sha256']!=validation['bitstream_sha256'] or hashlib.sha256(fs.read_bytes()).hexdigest()!=validation['bitstream_sha256']:
        raise SystemExit('Program and verify the matching M4 build with board_test.py first')
    summary={'started_utc':datetime.now(timezone.utc).isoformat(),'requested_seconds':args.seconds,
        'port':args.port,'bitstream_sha256':validation['bitstream_sha256'],
        'firmware_sha256':validation['firmware_sha256'],'clock_hz':validation['clock_hz'],'cycles':0,
        'memory_copy_bytes':0,'logical_memory_access_bytes':0,'sd_read_bytes':0,'spi_lcd_updates':0,
        'frame_flips':0,'frame_checks':0,'max_command_seconds':{},'underflows':0,
        'status':'running','passed':False,'sd_writes':False,'visual_confirmation':'pending'}
    begin=time.monotonic()
    def save():
        summary['elapsed_seconds']=time.monotonic()-begin
        path=output/f'{args.name}-validation.json'
        temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
        temporary.replace(path)
    with (output/f'{args.name}-uart.log').open('w',encoding='utf-8') as log, (output/f'{args.name}-cycles.jsonl').open('w',encoding='utf-8') as metrics:
        try:
            with keep_host_awake() as sleep_prevented, serial.Serial(args.port,115200,timeout=.1) as port:
                summary['host_system_sleep_prevented']=sleep_prevented
                port.reset_input_buffer()
                def command(cmd,expected):
                    start=time.monotonic();port.write((cmd+'\r').encode());port.flush()
                    data=bytearray()
                    while time.monotonic()-start<15:
                        data.extend(port.read(4096))
                        if b'> ' in data: break
                    text=data.decode(errors='replace')
                    duration=time.monotonic()-start
                    log.write(f'[{time.monotonic()-begin:.3f}] {cmd}\n{text}');log.flush()
                    if expected not in text or '> ' not in text or 'FAIL' in text or 'failed' in text:
                        raise RuntimeError(f'{cmd}: invalid response: {text[-800:]}')
                    key=cmd.split()[0]
                    summary['max_command_seconds'][key]=max(duration,summary['max_command_seconds'].get(key,0))
                    return text
                command('fbmemory','LCD DDR framebuffer enabled')
                first=previous=parse_status(command('fbcheck','FB CHECK PASS'))
                summary['first_frame_status']=first
                # Duration starts after the preflight, so it covers full stress cycles.
                begin=time.monotonic();next_progress=0
                summary['stress_started_utc']=datetime.now(timezone.utc).isoformat()
                while time.monotonic()-begin<args.seconds:
                    copy_ticks=[]
                    for _ in range(4):
                        text=command('memcopy','MEM COPY PASS: bytes=32768 ticks=')
                        match=re.search(r'ticks=([0-9a-f]{8})',text)
                        if not match: raise RuntimeError('Missing memory timing')
                        copy_ticks.append(int(match.group(1),16))
                        summary['memory_copy_bytes']+=32768;summary['logical_memory_access_bytes']+=5*32768
                    command('sdcheck RVTEST00.BIN','SD READ PASS: RVTEST00.BIN bytes=4096 CRC32=08040e1e')
                    summary['sd_read_bytes']+=4096
                    command('lcd','LCD transfer PASS:');summary['spi_lcd_updates']+=1
                    command('fbflip','RGB LCD FLIP PASS:');summary['frame_flips']+=1
                    current=parse_status(command('fbcheck','FB CHECK PASS'));summary['frame_checks']+=1
                    if current['completed']<=previous['completed'] or current['frames']<=previous['frames'] or current['active']==previous['active']:
                        raise RuntimeError(f'Frame stopped/reset/flip not observed: {current}')
                    previous=current;summary['last_frame_status']=current;summary['cycles']+=1
                    summary['last_copy_ticks']=copy_ticks
                    metrics.write(json.dumps({'seconds':time.monotonic()-begin,'cycle':summary['cycles'],'frame':current,'copy_ticks':copy_ticks})+'\n');metrics.flush();save()
                    if time.monotonic()-begin>=next_progress:
                        print(f"{summary['elapsed_seconds']:.1f}s cycles={summary['cycles']} copied={summary['memory_copy_bytes']} SD={summary['sd_read_bytes']} underflows=0",flush=True)
                        next_progress=time.monotonic()-begin+60
                summary['status']='completed';summary['passed']=True
                summary['completed_utc']=datetime.now(timezone.utc).isoformat()
        except BaseException as error:
            summary['status']='failed';summary['error']=str(error)
            raise
        finally:
            save()
    print(f"PASS: {summary['elapsed_seconds']:.1f}s / {summary['cycles']} cycles; {output / (args.name+'-validation.json')}",flush=True)

if __name__=='__main__': main()
