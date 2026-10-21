"""Flash/UART boot client. Flash installation and FPGA programming are explicit."""
import argparse
import hashlib
import json
import re
import struct
import subprocess
import sys
import time
from pathlib import Path
import serial
from tools.boot_image import CHUNK, HEADER, packet, unpack_image
from tools.boot_session import BootSession, programmer_succeeded
from tools.programmer_cable import resolve_location

ROOT=Path(__file__).resolve().parents[1]

def verified_output(output):
    validation=json.loads((output/'validation.json').read_text())
    if validation.get('synthesis_requested') is not True or validation.get('timing_violated_endpoints') != {'setup':0,'hold':0}:
        raise RuntimeError('Successful PnR and timing validation required')
    fs=output/'gateware/riscv_mini.fs'
    if hashlib.sha256(fs.read_bytes()).hexdigest()!=validation['bitstream_sha256']:
        raise RuntimeError('Bitstream changed since validation')
    return validation,fs

def require_install_supported(validation):
    if validation.get('boot_mode','rom')=='xip':
        raise ValueError('XIP loader cannot install through UART. Use scripts/mini.py board update --build <ID>; no hardware was touched.')
    if not validation.get('features',{}).get('flash',True):
        raise ValueError('Selected build has no Flash installation support')

def program(output,location,configuration=False,log_dir=None):
    validation,fs=verified_output(output)
    logs=Path(log_dir or output);logs.mkdir(parents=True,exist_ok=True)
    if configuration:
        require_install_supported(validation)
        from mini_ops.artifacts import configuration_bytes
        if configuration_bytes(fs)>=0x200000:
            raise RuntimeError('Configuration image must fit below the firmware partition')
    tools=json.loads((ROOT/'.tools.local.json').read_text())
    selected_location=resolve_location(tools['programmer'],location,log_path=logs/'programmer-cables.log')
    command=[tools['programmer'],'--location',selected_location,'--frequency','2MHz',
             '--device','GW2A-18C','--operation_index','8' if configuration else '2','--fsFile',str(fs)]
    if configuration: command+=['--spiaddr','0x000000']
    log=logs/('configuration-programmer.log' if configuration else 'programmer.log')
    # Preserve programmer output; Flash uses erase/program, without Verify.
    with log.open('w',encoding='utf-8') as stream:
        result=subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,timeout=120)
    result.stdout=log.read_text(encoding='utf-8',errors='replace');result.stderr=''
    if not programmer_succeeded(result):
        raise RuntimeError('Programming failed; inspect programmer log')
    if configuration:
        reload_command=command[:command.index('--operation_index')]+['--operation_index','1']
        reload_command[reload_command.index('--location')+1]=resolve_location(
            tools['programmer'],location,log_path=logs/'configuration-reload-cables.log')
        result=subprocess.run(reload_command,capture_output=True,text=True,timeout=120)
        (logs/'configuration-reload.log').write_text(result.stdout+result.stderr,encoding='utf-8')
        if not programmer_succeeded(result):raise RuntimeError('FPGA Flash reload failed; inspect reload log')
    return validation

def main():
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',default='COM4')
    p.add_argument('--location',default=None,help='Gowin cable location override; default scans the connected JTAG cable before programming')
    p.add_argument('--output-dir',type=Path,default=ROOT/'build/base')
    p.add_argument('--image',type=Path)
    p.add_argument('--log-dir',type=Path,help='Operation-specific logs; legacy default is output-dir')
    p.add_argument('--mode',choices=('uart','install','flash','info'),default='uart')
    p.add_argument('--program',action='store_true',help='Download FPGA SRAM before loading')
    p.add_argument('--configure-flash',action='store_true',help='Replace FPGA Flash configuration; requires --mode install')
    p.add_argument('--reset',action='store_true',help='Reset a running application before entering the loader')
    a=p.parse_args();output=a.output_dir.resolve()
    if a.configure_flash and (a.mode!='install' or a.program or a.reset):
        p.error('--configure-flash requires --mode install without --program/--reset; configuration is programmed first, firmware last')
    validation,fs=verified_output(output)
    if a.mode=='install':require_install_supported(validation)
    image=None
    if a.mode in ('uart','install'):
        image=(a.image or output/'firmware/app.img').read_bytes()
        unpack_image(image,validation['boot_image']['abi_tag'])
    logs=(a.log_dir or output).resolve();logs.mkdir(parents=True,exist_ok=True)
    with (logs/'boot-upload-uart.log').open('wb') as log, serial.Serial(a.port,115200,timeout=.1) as port:
        port.reset_input_buffer()
        if a.program or a.configure_flash: program(output,a.location,a.configure_flash,logs)
        session=BootSession(port,log)
        print(session.menu(a.reset).decode(errors='replace'),end='',flush=True)
        if a.mode=='info': result=session.command('i')
        elif a.mode=='flash':
            port.write(b'f');result=session.until(b'> ')
            if b'BOOT FLASH' not in result or b'ERR ' in result: raise RuntimeError(result.decode(errors='replace'))
        else:
            result=session.upload(image,a.mode=='install')
        print(result.decode(errors='replace'),end='',flush=True)

if __name__=='__main__': main()
