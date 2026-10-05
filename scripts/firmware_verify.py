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
    parser.add_argument('--mic',action='store_true',help='Also test the externally connected microphone(s)')
    args=parser.parse_args()
    if not 0<=args.soak_seconds<=300:parser.error('Use 0..300 seconds')
    if args.program and args.reset:parser.error('Use --program or --reset')
    output=args.output_dir.resolve();validation,_=verified_output(output)
    features=validation.get('features',{})
    def enabled(name):return features.get(name,True)
    if args.sd_write and not enabled('filesystem'):parser.error('--sd-write requires filesystem')
    if args.soak_seconds and not all(enabled(n) for n in ('filesystem','video','usb','audio')):parser.error('Soak requires filesystem/video/usb/audio')
    if args.mic and not enabled('mic'):parser.error('--mic requires mic')
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
            cases=[('isa',True),('l2',bool(validation.get('l2_size_bytes'))),('fence',True),('uart',True),('irq',True),('ddr',True),('flash',enabled('flash')),
                ('sd',enabled('filesystem')),('sd blocks',enabled('sd')),('lcd',enabled('video')),
                ('lcd start',enabled('video')),('lcd stop',enabled('video')),
                ('lcd clear',enabled('video')),('spi-lcd',enabled('spi_lcd')),
                ('io',enabled('board_io') or enabled('ws2812')),('audio',enabled('audio')),
                ('eth',enabled('eth')),('eth parser',enabled('eth')),('usb',enabled('usb')),('usb tree',enabled('usb')),('usb stop',enabled('usb'))]
            cases += [('dma',validation.get('dma_backend')=='native')]
            capabilities = validation.get('cpu_capabilities')
            if capabilities is not None:
                cases=[('fpu',capabilities['fpu']),('mmu',capabilities['mmu']),*cases]
            elif validation.get('cpu_variant')=='linux' and validation.get('cpu_verilog'):
                cases=[('fpu',True),('mmu',True),*cases]
            for name,available in cases:
                command('test '+name,None if available else 'UNSUPPORTED: L2 disabled' if name=='l2' else 'UNSUPPORTED: feature disabled')
            if enabled('usb'):
                command('test usb','TEST usb FAIL')
                for _ in range(3):command('test usb restart')
            if enabled('usb') or enabled('eth'):command('test phys')
            if enabled('usb'):
                command('test usb input','TEST usb input READY');command('test usb input stop')
            for name in ('audio start','audio pause','audio resume','audio stop','eth start','eth stop'):
                if enabled('audio' if name.startswith('audio') else 'eth'):command('test '+name)
            invalids=['unknown']
            if all(enabled(n) for n in ('filesystem','video','usb','audio')):invalids+=['soak 0','soak 301']
            if enabled('usb'):invalids+=['usb leds 1 8 0','usb leds 1']
            for invalid in invalids:
                command('test '+invalid,'ERR test command/argument')
            if args.mic:
                command('test mic')
                if enabled('mic_stereo'):command('test mic stereo')
            if args.sd_write:command('test sd write')
            if args.soak_seconds:command(f'test soak {args.soak_seconds}',timeout=args.soak_seconds+90)
            for name,feature in [('lcd clear','video'),('audio stop','audio'),('eth stop','eth')]:
                if enabled(feature):command('test '+name)
            command('status','CPU/sys=60 MHz DDR=120 MHz')
            report['passed']=True
    finally:
        (output/'firmware-verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('Firmware command verification PASS',flush=True)

if __name__=='__main__':main()
