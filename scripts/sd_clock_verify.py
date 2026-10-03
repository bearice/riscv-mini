"""Read-only native SD clock sweep against a low-speed CRC reference.

Failure candidates are recorded and followed by a fresh low-speed initialization.
No card writes, host network changes, Flash programming or device unplugging.
"""
import argparse
import json
import re
import time
from pathlib import Path
import serial
from boot_upload import BootSession
from benchmark import ROW

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',default='COM4')
    p.add_argument('--rounds',type=int,default=3)
    p.add_argument('--clocks',type=int,nargs='+',default=[7500000,10000000,15000000,30000000])
    p.add_argument('--soak-seconds',type=int,default=0)
    p.add_argument('--output-dir',type=Path,default=Path('build/libc-sd/clock-results'))
    a=p.parse_args()
    if not 1<=a.rounds<=10 or not 0<=a.soak_seconds<=300:p.error('rounds1..10, soak0..300 seconds')
    if any(hz not in (7500000,10000000,15000000,30000000) for hz in a.clocks):p.error('Unsupported frequency')
    if a.soak_seconds and len(a.clocks)!=1:p.error('Soak takes one clock')
    a.output_dir.mkdir(parents=True,exist_ok=True);results=[];reference=None
    with (a.output_dir/'uart.log').open('wb') as log,serial.Serial(a.port,115200,timeout=.1) as port:
        session=BootSession(port,log)
        def command(text):
            port.write((text+'\r').encode());port.flush();return session.until(b'> ',120)
        port.reset_input_buffer();command('status')
        start=time.monotonic();round_=0
        while round_<a.rounds or (a.soak_seconds and time.monotonic()-start<a.soak_seconds):
            for hz in a.clocks:
                data=command('bench sd '+str(hz));rows=[]
                for match in ROW.finditer(data):
                    name,size,count,ticks,check,passed=match.groups()
                    rows.append(dict(name=name.decode(),size=int(size),count=int(count),ticks=int(ticks),crc=check.decode(),passed=passed==b'PASS'))
                ok=b'BENCH DONE PASS' in data and len(rows)==2 and all(r['name']=='io.sd-read' and r['passed'] and r['ticks'] for r in rows)
                if ok:
                    if reference is None:reference=rows[0]['crc']
                    ok=all(r['crc']==reference for r in rows)
                    for r in rows:r['MiB_per_second']=r['size']*r['count']*60000000/r['ticks']/1048576
                info=re.search(rb'BENCH SD hz=(\d+).*?hs=(\d+)',data)
                record=dict(round=round_+1,clock_hz=hz,passed=ok,rows=rows,high_speed=int(info[2]) if info else None)
                results.append(record)
                (a.output_dir/'results.json').write_text(json.dumps({'reference_crc':reference,'elapsed_seconds':time.monotonic()-start,'results':results},indent=2)+'\n')
                print(f'round={round_+1} hz={hz} PASS={ok} '+repr(rows),flush=True)
                if not ok and a.soak_seconds:raise RuntimeError('SD soak failure')
            round_+=1
        recovery=command('bench sd 7500000')
        if b'BENCH DONE PASS' not in recovery:raise RuntimeError('Low-speed recovery failed')
        if b'BIOS TEST PASS' not in command('test bios'):raise RuntimeError('BIOS self-test failed')
        print(command('status').decode(errors='replace'),flush=True)
    print('SD sweep finished; rejected candidates remain unqualified',flush=True)

if __name__=='__main__':main()
