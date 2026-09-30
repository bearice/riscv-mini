"""Load the independent SD demo; verify files/blocks and bounded LCD concurrency.

--write-test creates one new RV6Txxxx.BIN, never overwrites an existing file.
"""
import argparse
import json
import re
import time
import zlib
from pathlib import Path
import serial
from boot_image import unpack_image
from boot_upload import BootSession,program,verified_output

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'build/m6')
    parser.add_argument('--image',type=Path,default=ROOT/'build/m6-demo/firmware/app.img')
    parser.add_argument('--port',default='COM4')
    parser.add_argument('--location',default='107569')
    parser.add_argument('--program',action='store_true')
    parser.add_argument('--reset',action='store_true')
    parser.add_argument('--write-test',action='store_true')
    parser.add_argument('--soak-seconds',type=float,default=300)
    args=parser.parse_args();output=args.output_dir.resolve()
    if args.soak_seconds<0:parser.error('soak duration must be nonnegative')
    validation,_=verified_output(output)
    image=args.image.read_bytes();unpack_image(image,validation['boot_image']['abi_tag'])
    report={'passed':False,'sd_backend':validation['sd_backend'],'write_test':args.write_test,'checks':{}}
    try:
        with (output/'sd-verification-uart.log').open('wb') as log,serial.Serial(args.port,115200,timeout=.05) as port:
            port.reset_input_buffer()
            if args.program:program(output,args.location)
            session=BootSession(port,log);session.menu(args.reset)
            startup=session.upload(image)
            if b'SD DEMO:' not in startup:raise RuntimeError('Load firmware/examples/sd_demo.c')
            report['checks']['startup']=startup.decode(errors='replace')
            def command(letter):
                port.write(letter.encode());port.flush();result=session.until(b'> ',30)
                if b' FAIL' in result:raise RuntimeError(result.decode(errors='replace'))
                return result
            def status():
                text=command('s')
                match=re.search(rb'SD native=([0-9a-f]{8}) width=([0-9a-f]{8}) hz=([0-9a-f]{8}) ready=([0-9a-f]{8})',text)
                if not match:raise RuntimeError('Missing SD status')
                values=tuple(int(match[i],16) for i in range(1,5))
                expected=(1,4,7500000,1) if validation['sd_backend']=='native' else (0,1,6000000,1)
                if values!=expected or b'underflows=00000000' not in text or b'drops=00000000 unhandled=00000000' not in text:
                    raise RuntimeError('SD/display/IRQ status: '+text.decode(errors='replace'))
                if b'errors=00000000' not in text:raise RuntimeError('SD controller recorded errors')
                return text
            report['checks']['status']=status().decode(errors='replace')
            read=command('r');match=re.search(rb'SD READ PASS bytes=([0-9a-f]{8}) crc=([0-9a-f]{8})',read)
            if not match:raise RuntimeError('Existing file CRC not reported')
            reference=(int(match[1],16),int(match[2],16));report['existing_file']={'bytes':reference[0],'crc32':f'{reference[1]:08x}'}
            blocks=command('b')
            if b'SD BLOCK PASS single/multiple/unaligned CRC=' not in blocks:raise RuntimeError('Block comparison result not reported')
            report['checks']['blocks']=blocks.decode(errors='replace')
            if args.write_test:
                write=command('w')
                match=re.search(rb'SD WRITE PASS file=(RV6T[0-9]{4}\.BIN) bytes=00010000 crc=([0-9a-f]{8})',write)
                expected=zlib.crc32(bytes(((offset*17)^(offset>>8)^0x5a)&255 for offset in range(65536)))
                if not match or int(match[2],16)!=expected:raise RuntimeError('New file CRC mismatch')
                report['new_file']={'name':match[1].decode(),'bytes':65536,'crc32':f'{expected:08x}'}
                print('Write/read passed: '+json.dumps(report['new_file']),flush=True)
            started=time.monotonic();rounds=0;first=None;last=None
            while time.monotonic()-started<args.soak_seconds:
                command('f');read=command('r')
                match=re.search(rb'SD READ PASS bytes=([0-9a-f]{8}) crc=([0-9a-f]{8})',read)
                if not match or (int(match[1],16),int(match[2],16))!=reference:raise RuntimeError('Existing file CRC changed')
                text=status();counter=re.search(rb'frames=([0-9a-f]{8}) completed=([0-9a-f]{8})',text)
                if not counter:raise RuntimeError('Missing LCD counters')
                current=tuple(int(counter[i],16) for i in (1,2))
                if last is not None and any(((n-p)&0xffffffff)==0 for n,p in zip(current,last)):raise RuntimeError('LCD counters stopped')
                if first is None:first=current
                last=current;rounds+=1
                if rounds%10==0:print(f'{rounds} rounds, {time.monotonic()-started:.1f}s, SD CRC/LCD/IRQ OK',flush=True)
                remaining=args.soak_seconds-(time.monotonic()-started)
                if remaining>0:time.sleep(min(2,remaining))
            report['soak']={'seconds':time.monotonic()-started,'rounds':rounds,'first_counters':first,'last_counters':last}
            report['checks']['final_status']=status().decode(errors='replace')
            report['passed']=True
    finally:
        (output/'sd-verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('SD verification passed.',flush=True)

if __name__=='__main__':main()
