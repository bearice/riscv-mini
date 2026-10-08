"""Hardware acceptance for loader boundaries, UART execution and optional Flash install."""
import argparse
import hashlib
import json
import re
import struct
import sys
import time
import zlib
from pathlib import Path
import serial
from boot_image import HEADER, LOAD, pack_image, packet, unpack_image
from boot_upload import BootSession, program, verified_output

ROOT=Path(__file__).resolve().parents[1]

def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,default=ROOT/'build/base')
    p.add_argument('--port',default='COM4')
    p.add_argument('--location',default='107569')
    p.add_argument('--program',action='store_true')
    p.add_argument('--reset',action='store_true')
    p.add_argument('--install',action='store_true',help='Write the generated firmware image to [2,4) MiB Flash')
    p.add_argument('--soak-seconds',type=int,default=0,help='Poll status/SD directory while the DDR display runs')
    a=p.parse_args();output=a.output_dir.resolve()
    if not 0<=a.soak_seconds<=300:p.error('--soak-seconds must be between 0 and 300')
    validation,_=verified_output(output);image=(output/'firmware/app.img').read_bytes()
    header,payload=unpack_image(image,validation['boot_image']['abi_tag'])
    report={'bitstream_sha256':validation['bitstream_sha256'],'image_sha256':hashlib.sha256(image).hexdigest(),
            'clock_hz':validation['clock_hz'],'flash_write_requested':a.install,'checks':{},'passed':False}
    def record(name,text):
        report['checks'][name]=text.decode(errors='replace')
        print(name+': '+text.decode(errors='replace')[-180:].strip(),flush=True)
    try:
        with (output/'boot-verification-uart.log').open('wb') as log, serial.Serial(a.port,115200,timeout=.05) as port:
            port.reset_input_buffer()
            if a.program: program(output,a.location)
            s=BootSession(port,log);record('startup',s.menu(a.reset))
            info=s.command('i');record('flash_info',info)
            if b'READY' not in info: raise RuntimeError('Flash not recognized')
            def bad_header(name,data):
                port.write(b'u');s.until(b'READY HEADER\r\n');port.write(data);port.flush()
                result=s.until(b'BL> ')
                if b'ERR HEADER' not in result: raise RuntimeError(name+' not rejected')
                record(name,result)
            changed=bytearray(image[:HEADER.size]);changed[0]^=1;bad_header('header_crc',changed)
            for name,index,value in [('abi',3,header[3]^1),('load_rom',5,0),('load_framebuffer',5,0x07e00000),
                    ('zero_length',4,0),('oversized',4,0xffffffff),('entry_unaligned',6,LOAD+1),('entry_outside',6,LOAD+len(payload)),('flags',8,1)]:
                words=list(header);words[index]=value;words[-1]=zlib.crc32(HEADER.pack(*words)[:-4])
                bad_header(name,HEADER.pack(*words))
            port.write(b'u');s.until(b'READY HEADER\r\n');port.write(image[:HEADER.size]);s.until(b'READY DATA\r\n')
            bad=bytearray(packet(0,payload[:128]));bad[-1]^=1;port.write(bad)
            rejected=s.until(b'BL> ')
            if b'ERR PACKET CRC' not in rejected: raise RuntimeError('Packet CRC was not rejected')
            record('packet_crc',rejected)
            port.write(b'u');s.until(b'READY HEADER\r\n');port.write(image[:12]);port.flush()
            timeout=s.until(b'BL> ',10)
            if b'ERR UART TIMEOUT' not in timeout: raise RuntimeError('Truncated header did not time out')
            record('uart_timeout',timeout)
            # Header/payload CRC disagreement must be caught even with valid packet CRCs.
            wrong=bytearray(payload);wrong[-1]^=1
            bad_image=image[:HEADER.size]+wrong
            port.write(b'u');s.until(b'READY HEADER\r\n');port.write(bad_image[:HEADER.size]);s.until(b'READY DATA\r\n')
            for seq,start in enumerate(range(0,len(wrong),128)):
                port.write(packet(seq,wrong[start:start+128]));port.flush()
                if s.until(b'K',5)!=b'K': raise RuntimeError('Unexpected ACK')
                ack=port.read(4)
                if ack!=struct.pack('<I',seq): raise RuntimeError('ACK sequence')
            rejected=s.until(b'BL> ')
            if b'ERR PAYLOAD CRC' not in rejected: raise RuntimeError('Payload was not rejected')
            record('payload_crc',rejected)
            app=s.upload(image);record('uart_boot',app)
            ready=b'SYSTEM READY sd=00000001 spi_lcd=00000001 rgb_lcd=00000001'
            audio_idle=f'AUDIO hz={validation["audio_sample_rate"]:08x} control=00000004 level=00000000 underruns=00000000 errors=00000000 amp=00000000'.encode()
            def healthy(text):
                if b'RISCV MINI BIOS' in text:
                    if any(marker not in text for marker in (b'SYSTEM READY - BIOS',b'POST SD PASS',b'POST RGB LCD PASS')):return False
                    port.write(b'status\r');status=s.until(b'> ',30);record('bios_idle_status',status)
                    return b'underflows=00000000' in status and audio_idle in status
                return ready in text and b'underflows=00000000' in text and audio_idle in text
            if not healthy(app): raise RuntimeError('Peripheral/audio idle regression')
            for command,marker in [(b'status\r',b'FLASH JEDEC=000b4017'),(b'ls\r',b'RVTEST00.BIN'),(b'help\r',b'help, status')]:
                port.write(command);response=s.until(b'> ',30)
                if marker not in response or b' FAIL' in response: raise RuntimeError(response)
                record(command.decode().strip(),response)
            record('uart_reset',s.menu(True))
            if a.install:
                record('flash_install',s.upload(image,True))
                port.write(b'f');app=s.until(b'> ')
                if b'BOOT FLASH' not in app or not healthy(app):raise RuntimeError('Flash boot/audio idle failed')
                record('flash_boot',app)
                for index in range(2):
                    port.write(b'!');port.flush();app=s.until(b'> ',120)
                    if b'BOOT FLASH' not in app or not healthy(app):raise RuntimeError('Automatic Flash reboot/audio idle failed')
                    record(f'flash_auto_reset_{index+1}',app)
            else: record('uart_final',s.upload(image))
            if a.soak_seconds:
                started=time.monotonic();rounds=0;first=None;last=None
                while time.monotonic()-started<a.soak_seconds:
                    port.write(b'status\r');response=s.until(b'> ',30)
                    counters=re.search(rb'frames=([0-9a-f]{8}) completed=([0-9a-f]{8}) underflows=([0-9a-f]{8})',response)
                    if not counters or int(counters[3],16) or b'UNAVAILABLE' in response:
                        raise RuntimeError('Soak status failed: '+response.decode(errors='replace'))
                    now=tuple(int(counters[i],16) for i in (1,2))
                    if first is None:first=now
                    if last is not None and any(((n-p)&0xffffffff)==0 for n,p in zip(now,last)):
                        raise RuntimeError('LCD counters stopped advancing')
                    last=now
                    port.write(b'ls\r');listing=s.until(b'> ',30)
                    if b'RVTEST00.BIN' not in listing or b'failed' in listing:
                        raise RuntimeError('Soak SD directory read failed')
                    rounds+=1
                    remaining=a.soak_seconds-(time.monotonic()-started)
                    if remaining>0:time.sleep(min(5,remaining))
                report['soak']={'elapsed_seconds':time.monotonic()-started,'rounds':rounds,
                    'first_counters':first,'last_counters':last,'underflows':0,'sd_writes':0}
                print('Soak passed: '+json.dumps(report['soak']),flush=True)
            report['passed']=True
    finally:
        (output/'boot-verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('Boot verification passed.',flush=True)

if __name__=='__main__':main()
