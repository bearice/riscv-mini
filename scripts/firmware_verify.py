"""Drive the monitor's on-board test commands; host owns UART transport only.

No USB unplug, physical input, audible output, Flash writing or NIC changes.
--sd-write explicitly creates a new file; --soak-seconds is limited to five minutes.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path
import serial
from boot_image import unpack_image
from boot_upload import BootSession,program,verified_output

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'build/base')
    parser.add_argument('--image',type=Path)
    parser.add_argument('--port',default='COM4');parser.add_argument('--location',default='107569')
    parser.add_argument('--program',action='store_true');parser.add_argument('--reset',action='store_true')
    parser.add_argument('--sd-write',action='store_true')
    parser.add_argument('--soak-seconds',type=int,default=0)
    args=parser.parse_args()
    if not 0<=args.soak_seconds<=300:parser.error('Use 0..300 seconds')
    if args.program and args.reset:parser.error('Use --program or --reset')
    output=args.output_dir.resolve();validation,_=verified_output(output)
    image=(args.image or output/'firmware/app.img').read_bytes()
    unpack_image(image,validation['boot_image']['abi_tag'])
    report={'passed':False,'bitstream_sha256':validation['bitstream_sha256'],
        'image_sha256':hashlib.sha256(image).hexdigest(),'checks':{},'sd_write_requested':args.sd_write,
        'excluded':'physical keyboard/mouse/LEDs, screen observation, audible output, external Ethernet packets; RTL/PnR remain host checks'}
    try:
        with serial.Serial(args.port,115200,timeout=.05) as port,(output/'firmware-verification-uart.log').open('wb') as log:
            port.reset_input_buffer()
            if args.program:program(output,args.location)
            session=BootSession(port,log);session.menu(args.reset)
            report['startup']=session.upload(image).decode(errors='replace')
            def command(name,expected=None,timeout=120):
                started=time.monotonic();port.write((name+'\r').encode());port.flush()
                text=session.until(b'> ',timeout)
                marker=(expected or 'TEST '+name[5:]+' PASS').encode()
                report['checks'].setdefault(name,[]).append({'seconds':time.monotonic()-started,
                    'expected':marker.decode(),'matched':marker in text,'output':text.decode(errors='replace')})
                if marker not in text:raise RuntimeError(name+': '+text.decode(errors='replace'))
                print(name+' OK',flush=True)
            for name in ('uart','irq','ddr','flash','sd','sd blocks','lcd','lcd clear','spi-lcd',
                         'io','audio','eth','eth parser','usb','usb stop'):
                command('test '+name)
            command('test usb','TEST usb FAIL')
            for _ in range(3):command('test usb restart')
            command('test phys')
            command('test usb input','TEST usb input READY');command('test usb input stop')
            for name in ('audio start','audio pause','audio resume','audio stop','eth start','eth stop'):
                command('test '+name)
            for invalid in ('soak 0','soak 301','usb leds 1 4 0','usb leds 1','unknown'):
                command('test '+invalid,'ERR test command/argument')
            if args.sd_write:command('test sd write')
            if args.soak_seconds:
                command(f'test soak {args.soak_seconds}',timeout=args.soak_seconds+90)
            command('test lcd clear');command('test audio stop');command('test eth stop')
            command('status','CPU/sys=60 MHz DDR=120 MHz')
            report['passed']=True
    finally:
        (output/'firmware-verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('Firmware command verification PASS',flush=True)

if __name__=='__main__':main()
