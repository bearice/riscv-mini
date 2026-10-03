"""Run BIOS bench commands and save measured ticks, rates and UART evidence.

Read-only storage tests; no Flash update, network reconfiguration or USB removal.
"""
import argparse
import concurrent.futures
import json
import re
import statistics
import time
import zlib
from pathlib import Path
import serial
from boot_upload import BootSession
from bios_tftp import PayloadServer

ROW = re.compile(rb'BENCH ([\w.-]+) size=(\d+) count=(\d+) ticks=(\d+) check=([0-9a-f]{8}) (PASS|FAIL)')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',default='COM4')
    p.add_argument('--rounds',type=int,default=3)
    p.add_argument('--suite',choices=('all','cpu','mem','io'),default='all')
    p.add_argument('--host-ip',help='enable 1 MiB real TFTP receive benchmark')
    p.add_argument('--output-dir',type=Path,default=Path('build/benchmark/results'))
    a=p.parse_args()
    if not 1<=a.rounds<=10:p.error('--rounds must be 1..10')
    a.output_dir.mkdir(parents=True,exist_ok=True)
    rows=[]
    with (a.output_dir/'uart.log').open('wb') as log, serial.Serial(a.port,115200,timeout=.1) as port:
        session=BootSession(port,log)
        def command(cmd,timeout=180):
            port.write((cmd+'\r').encode());port.flush()
            data=session.until(b'> ',timeout)
            print(data.decode(errors='replace'),flush=True)
            return data
        port.reset_input_buffer();command('status')
        # Prime the console so setup/redrawing stays outside the timed kernels.
        time.sleep(1)
        for round_ in range(a.rounds):
            print(f'Round {round_+1}/{a.rounds}',flush=True)
            data=command('bench '+a.suite)
            if b'BENCH DONE PASS' not in data:raise RuntimeError('Benchmark failed')
            expected=0
            if a.host_ip:
                data_bytes=bytes(range(256))*4096
                expected=zlib.crc32(data_bytes)
                server=PayloadServer(a.host_ip,data_bytes,'BENCH.BIN')
                try:
                    command('set server '+a.host_ip)
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        future=pool.submit(server.serve_once)
                        net=command('bench net BENCH.BIN',60)
                        if not future.result(timeout=5) or b'BENCH DONE PASS' not in net:raise RuntimeError('TFTP failed')
                        data+=net
                    log.write(('TFTP '+repr(server.events)+'\n').encode())
                finally:server.close()
            for match in ROW.finditer(data):
                name,size,count,ticks,check,passed=match.groups()
                row=dict(round=round_+1,name=name.decode(),size=int(size),count=int(count),ticks=int(ticks),check=check.decode(),passed=passed==b'PASS')
                if not row['ticks'] or not row['passed']:raise RuntimeError(f'Invalid measurement: {row}')
                row['seconds']=row['ticks']/60000000
                if name.startswith(b'cpu.'):
                    row['iterations_per_second']=row['count']/row['seconds']
                    row['cycles_per_iteration']=row['ticks']/row['count']
                elif name==b'mem.chase32':row['ns_per_load']=row['seconds']*1e9/row['count']
                else:row['MiB_per_second']=row['size']*row['count']/row['seconds']/1048576
                if name==b'io.tftp-rx' and (row['size']!=1048576 or int(check,16)!=expected):raise RuntimeError('Network payload CRC mismatch')
                rows.append(row)
            (a.output_dir/'results.json').write_text(json.dumps({'clock_hz':60000000,'rows':rows},indent=2)+'\n')
        if b'BIOS TEST PASS' not in command('test bios'):raise RuntimeError('BIOS regression test failed')
        command('status')
    for name,size in sorted({(r['name'],r['size']) for r in rows}):
        group=[r for r in rows if (r['name'],r['size'])==(name,size)]
        metric=next(k for k in ('cycles_per_iteration','ns_per_load','MiB_per_second') if k in group[0])
        print(f'{name:24} size={size:8} median {metric}={statistics.median(r[metric] for r in group):.3f}')

if __name__=='__main__':main()
