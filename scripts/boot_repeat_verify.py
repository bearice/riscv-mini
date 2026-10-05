"""Repeat SRAM programming and software reset, training DDR and loading BIOS.

No Flash writes, power cycles, USB unplug or network configuration changes.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path
import serial
from boot_upload import BootSession, program, verified_output
from boot_image import unpack_image


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--port',default='COM4');p.add_argument('--location',default='107569')
    p.add_argument('--program-count',type=int,default=3);p.add_argument('--reset-count',type=int,default=5)
    args=p.parse_args()
    if not 0<=args.program_count<=10 or not 0<=args.reset_count<=10 or not args.program_count+args.reset_count:
        p.error('Use 0..10 repetitions of each, at least one total')
    output=args.output_dir.resolve();validation,_=verified_output(output)
    image=(output/'firmware/app.img').read_bytes();unpack_image(image,validation['boot_image']['abi_tag'])
    report={'passed':False,'bitstream_sha256':validation['bitstream_sha256'],
            'image_sha256':hashlib.sha256(image).hexdigest(),'runs':[],
            'excluded':'powercycle, Flash boot, electrical PHY measurements'}
    try:
        with serial.Serial(args.port,115200,timeout=.05) as port,(output/'boot-repeat-verification-uart.log').open('wb') as log:
            session=BootSession(port,log)
            for kind,count in [('program',args.program_count),('reset',args.reset_count)]:
                for index in range(count):
                    started=time.monotonic();port.reset_input_buffer()
                    if kind=='program':program(output,args.location)
                    boot=session.menu(reset=kind=='reset')
                    if b'DDR READY' not in boot or b'ERR ' in boot:raise RuntimeError(boot.decode(errors='replace'))
                    startup=session.upload(image)
                    required=['DDR scratch','TIMER','BIOS ecall']
                    for feature,names in [('flash',['FLASH']),('filesystem',['SD']),('video',['RGB LCD']),
                                          ('eth',['ETH PHY']),('usb',['USB PHY','USB HID'])]:
                        if validation['features'].get(feature):required+=names
                    if any(('POST '+name+' PASS').encode() not in startup for name in required):
                        raise RuntimeError('BIOS POST failed: '+startup.decode(errors='replace'))
                    report['runs'].append({'kind':kind,'index':index+1,'seconds':time.monotonic()-started,
                        'boot':boot.decode(errors='replace'),'startup':startup.decode(errors='replace')})
                    print(kind,index+1,'DDR training and BIOS POST PASS',flush=True)
            report['passed']=True
    finally:
        (output/'boot-repeat-verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('Repeated boot verification PASS',flush=True)

if __name__=='__main__':main()
