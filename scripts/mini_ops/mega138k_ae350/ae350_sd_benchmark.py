"""Time uninterrupted native SD reads against host monotonic time."""
import re
import time
import statistics


def parse(lines):
    samples={};config=None
    for text,stamp in lines:
        fields=text.split()
        if fields[:2]==['SD_BENCH','CONFIG'] and len(fields)==6:
            config=dict(zip(('sd_hz','width','configured_cpu_hz','cache_control'),(int(x,16) for x in fields[2:])))
        elif fields[:2]==['SD_BENCH','START'] and len(fields)==3:
            i=int(fields[2],16)
            if i in samples:raise ValueError('duplicate benchmark sample')
            samples[i]=dict(index=i,start=stamp)
        elif fields[:2]==['SD_BENCH','END'] and len(fields)==5:
            i,lo,hi=(int(x,16) for x in fields[2:]);s=samples[i]
            s.update(cycles=(hi<<32)|lo,wall_seconds=stamp-s['start'],bytes=1<<20,call_bytes=(512,4096,65536)[i//3])
        elif fields[:2]==['SD_BENCH','CHECK'] and len(fields)==6:
            i,crc,error,guards=(int(x,16) for x in fields[2:]);samples[i].update(crc32=crc,error=error,guards=guards)
    if not config or config['sd_hz'] not in (10000000,20000000,25000000,40000000,50000000) or config['width']!=4 or set(samples)!=set(range(9)):
        raise ValueError('incomplete benchmark/configuration')
    rows=[samples[i] for i in range(9)]
    if any(not s.get('cycles') or s.get('wall_seconds',0)<=0 or s.get('error')!=0 or s.get('guards')!=1 for s in rows):
        raise ValueError('failed/incomplete benchmark sample')
    if len({s['crc32'] for s in rows})!=1:raise ValueError('benchmark content mismatch')
    for s in rows:
        s['observed_cycle_hz']=s['cycles']/s['wall_seconds']
        # UART start and end have equal length only to a few characters;
        # retain host monotonic as the independent calibration/throughput.
        s['mib_s']=s['bytes']/(1<<20)/s['wall_seconds']
    return config,rows


def run(session,hardware,op,timeout):
    from gateware.machines.mega138k_ae350 import address_map as amap
    lines=[];line=bytearray();deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        byte=session.port.read(1)
        if not byte:continue
        session.note(byte);line.extend(byte)
        if byte==b'\n':
            value=line.decode(errors='replace').strip();line.clear()
            stamp=time.monotonic()
            lines.append((value,stamp));print(value,flush=True)
            op.record('benchmark-uart-line',line=value,monotonic_seconds=stamp)
            if value=='SD_BENCH DONE':break
    else:raise TimeoutError('SD benchmark UART deadline')
    op.record('benchmark-uart',lines=lines)
    cpu=hardware.halt()
    raw=cpu.read_memory(amap.RESULTS_BASE,32*4)
    words=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)]
    op.record('benchmark-status',words=words)
    if words[0]!=0x5342ffff or words[1] or words[6] or words[7]:
        raise RuntimeError('SD benchmark init/read/stop failed: '+repr(words[:8]))
    config,rows=parse(lines)
    groups=[dict(call_bytes=n,median_mib_s=statistics.median(s['mib_s'] for s in rows if s['call_bytes']==n)) for n in (512,4096,65536)]
    op.report.update(benchmark=dict(config=config,samples=rows,summary=groups),media_written=False,
                     timing='host monotonic START/END UART lines; uninterrupted CPU; raw rdcycle64 also retained',
                     scope='SD API throughput includes commands, hard DMAC, cache maintenance and bounce copy; CRC outside timed region')
    op.record('benchmark-results',words=words,groups=groups,passed=True)
    for row in groups:print(row,flush=True)
