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
from boot_image import CHUNK, HEADER, packet, unpack_image

ROOT=Path(__file__).resolve().parents[1]

def programmer_succeeded(result):
    # Gowin can emit "Error: SPI Verify failed!" and still return 0/Finished.
    output=result.stdout+'\n'+result.stderr
    return result.returncode==0 and bool(re.search(r'\bFinished[.!]',output)) and not re.search(r'\berror\s*:|\bfailed\b',output,re.I)

class BootSession:
    def __init__(self,port,log=None):
        self.port=port
        self.log=log
    def note(self,data):
        if self.log:
            self.log.write(data);self.log.flush()
    def until(self,marker,timeout=120):
        data=bytearray();deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            # Small reads keep ACK/command boundaries intact.
            chunk=self.port.read(1)
            if chunk:
                data.extend(chunk)
                if data.endswith(b'ERR DDR INIT/TIMEOUT; reset\r\n') or data.endswith(b'ERR DDR INIT; reset\r\n'):
                    self.note(data)
                    raise RuntimeError('DDR initialization failed; reset required')
                if data.endswith(marker):
                    self.note(data)
                    return bytes(data)
        self.note(data)
        raise TimeoutError(f'Waiting for {marker!r}; received {bytes(data[-1000:])!r}')
    def menu(self,reset=False):
        if reset:
            self.port.reset_input_buffer();self.port.write(b'!');self.port.flush()
        data=self.until(b'BOOT SELECT:')
        self.port.write(b'b');self.port.flush()
        return data+self.until(b'BL> ')
    def command(self,value,timeout=120):
        self.port.write(value.encode());self.port.flush()
        return self.until(b'BL> ',timeout)
    def upload(self,image,install=False):
        _,payload=unpack_image(image)
        self.port.write(b'p' if install else b'u');self.port.flush()
        self.until(b'READY HEADER\r\n')
        self.port.write(image[:HEADER.size]);self.port.flush()
        self.until(b'READY DATA\r\n')
        for sequence,start in enumerate(range(0,len(payload),CHUNK)):
            self.port.write(packet(sequence,payload[start:start+CHUNK]));self.port.flush()
            ack=bytearray();deadline=time.monotonic()+5
            while len(ack)<5 and time.monotonic()<deadline: ack.extend(self.port.read(5-len(ack)))
            if bytes(ack)!=b'K'+struct.pack('<I',sequence):
                self.note(ack)
                raise RuntimeError(f'Bad ACK at packet {sequence}: {bytes(ack)!r}')
        result=self.until(b'BL> ' if install else b'> ')
        if b'ERR ' in result or (install and b'FLASH INSTALLED' not in result):
            raise RuntimeError(result.decode(errors='replace'))
        if not install and b'SYSTEM READY' not in result:
            raise RuntimeError('Application did not become ready: '+result.decode(errors='replace'))
        return result

def verified_output(output):
    validation=json.loads((output/'validation.json').read_text())
    if not validation.get('synthesis_requested') or any(validation['timing_violated_endpoints'].values()):
        raise RuntimeError('Successful PnR and timing validation required')
    fs=output/'gateware/riscv_mini.fs'
    if hashlib.sha256(fs.read_bytes()).hexdigest()!=validation['bitstream_sha256']:
        raise RuntimeError('Bitstream changed since validation')
    return validation,fs

def program(output,location,configuration=False):
    validation,fs=verified_output(output)
    if configuration:
        raw=output/'gateware/impl/pnr/project.bin'
        if not raw.is_file() or raw.stat().st_size>=0x200000:
            raise RuntimeError('Configuration image must fit below the firmware partition')
    tools=json.loads((ROOT/'.tools.local.json').read_text())
    command=[tools['programmer'],'--cable-index','4','--location',str(location),'--frequency','2MHz',
             '--device','GW2A-18C','--operation_index','8' if configuration else '2','--fsFile',str(fs)]
    if configuration: command+=['--spiaddr','0x000000']
    log=output/('configuration-programmer.log' if configuration else 'programmer.log')
    # Preserve programmer output; Flash uses erase/program, without Verify.
    with log.open('w',encoding='utf-8') as stream:
        result=subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,timeout=120)
    result.stdout=log.read_text(encoding='utf-8',errors='replace');result.stderr=''
    if not programmer_succeeded(result):
        raise RuntimeError('Programming failed; inspect programmer log')
    if configuration:
        reload_command=command[:command.index('--operation_index')]+['--operation_index','1']
        result=subprocess.run(reload_command,capture_output=True,text=True,timeout=120)
        (output/'configuration-reload.log').write_text(result.stdout+result.stderr,encoding='utf-8')
        if not programmer_succeeded(result):raise RuntimeError('FPGA Flash reload failed; inspect reload log')
    return validation

def main():
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',default='COM4')
    p.add_argument('--location',default='107569')
    p.add_argument('--output-dir',type=Path,default=ROOT/'build/base')
    p.add_argument('--image',type=Path)
    p.add_argument('--mode',choices=('uart','install','flash','info'),default='uart')
    p.add_argument('--program',action='store_true',help='Download FPGA SRAM before loading')
    p.add_argument('--configure-flash',action='store_true',help='Replace FPGA Flash configuration; requires --mode install')
    p.add_argument('--reset',action='store_true',help='Reset a running application before entering the loader')
    a=p.parse_args();output=a.output_dir.resolve()
    if a.configure_flash and (a.mode!='install' or a.program or a.reset):
        p.error('--configure-flash requires --mode install without --program/--reset; configuration is programmed first, firmware last')
    validation,fs=verified_output(output)
    image=(a.image or output/'firmware/app.img').read_bytes()
    unpack_image(image,validation['boot_image']['abi_tag'])
    with (output/'boot-upload-uart.log').open('wb') as log, serial.Serial(a.port,115200,timeout=.1) as port:
        port.reset_input_buffer()
        if a.program or a.configure_flash: program(output,a.location,a.configure_flash)
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
