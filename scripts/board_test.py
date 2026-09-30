"""SRAM-download an already built stage and capture its UART acceptance log.

Explicit --program is required. Does not write FPGA configuration Flash.
USB location is machine-specific; override when cables change.
"""
import argparse
import hashlib
import json
import subprocess
import time
import re
import zlib
from pathlib import Path
import serial

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', choices=('m0','m1','m2','m3','m4'), default='m1')
    p.add_argument('--port', default='COM4')
    p.add_argument('--location', default='107569')
    p.add_argument('--program', action='store_true')
    p.add_argument('--timeout', type=float, default=120)
    p.add_argument('--soft-resets', type=int, default=0, help='M1: repeat acceptance via ! CPU reset')
    p.add_argument('--sd-write-test', action='store_true', help='M2: create a new SD test file and read it back')
    args=p.parse_args()
    if args.soft_resets < 0 or (args.stage == 'm0' and args.soft_resets):
        p.error('Soft resets require M1 and a nonnegative count.')
    if args.sd_write_test and args.stage not in ('m2','m3','m4'): p.error('SD write test requires M2.')
    if not args.program: p.error('Pass --program to authorize SRAM download.')
    output=ROOT/'build'/args.stage
    validation=json.loads((output/'validation.json').read_text())
    if not validation.get('synthesis_requested') or any(validation['timing_violated_endpoints'].values()):
        raise SystemExit('A successful synthesis/timing validation is required.')
    fs=output/'gateware/riscv_mini.fs'
    if hashlib.sha256(fs.read_bytes()).hexdigest()!=validation['bitstream_sha256']:
        raise SystemExit('Bitstream differs from verified build.')
    tools=json.loads((ROOT/'.tools.local.json').read_text())
    command=[tools['programmer'],'--cable-index','4','--location',args.location,
             '--frequency','2MHz','--device','GW2A-18C','--operation_index','2','--fsFile',str(fs)]
    with serial.Serial(args.port,115200,timeout=0.2) as port:
        port.reset_input_buffer()
        result=subprocess.run(command,capture_output=True,text=True,timeout=30)
        (output/'programmer.log').write_text(result.stdout+result.stderr,encoding='utf-8')
        if result.returncode or 'Finished.' not in result.stdout:
            raise SystemExit('SRAM programming failed; inspect programmer.log')
        print('SRAM download completed.',flush=True)
        captured=bytearray()
        deadline=time.monotonic()+args.timeout
        marker={'m0':b'> ','m1':b'M1 PASS:','m2':b'M2 READY:','m3':b'M3 READY:','m4':b'M4 READY:'}[args.stage]
        while time.monotonic()<deadline:
            chunk=port.read(4096)
            if chunk:
                captured.extend(chunk)
                print(chunk.decode('utf-8',errors='replace'),end='',flush=True)
            if marker in captured or b'DDR FAILED:' in captured or b'RGB LCD FAIL:' in captured: break
        port.write(b'board-echo-check\r')
        port.flush()
        echo=bytearray()
        deadline=time.monotonic()+2
        while time.monotonic()<deadline: echo.extend(port.read(4096))
        accepted=marker in captured
        if args.stage in ('m2','m3','m4'): accepted=accepted and b'M1 PASS:' in captured and b'SPI loopback PASS' in captured
        if args.stage in ('m3','m4'): accepted=accepted and b'RGB LCD DMA PASS:' in captured
        if args.stage=='m4': accepted=accepted and b'MEM COPY PASS:' in captured and b'MEM SOURCE FAIL' not in captured and b'MEM COPY FAIL' not in captured
        echo_ok=b'board-echo-check\r\n> ' in echo
        sd_log=bytearray()
        sd_pass=None
        if args.sd_write_test and accepted:
            port.write(b'sdtest\r'); port.flush()
            deadline=time.monotonic()+args.timeout
            while time.monotonic()<deadline:
                chunk=port.read(4096)
                if chunk:
                    sd_log.extend(chunk)
                    print(chunk.decode('utf-8',errors='replace'),end='',flush=True)
                if b'SD FILE FAIL' in sd_log or (b'SD FILE PASS:' in sd_log and b'> ' in sd_log): break
            expected=zlib.crc32(bytes(((i*73)^(i>>3)^0x5a)&255 for i in range(4096)))
            match=re.search(rb'SD FILE PASS: (RVTEST\d\d\.BIN) bytes=4096 CRC32=([0-9a-f]{8})',sd_log)
            sd_pass=bool(match and int(match.group(2),16)==expected)
            accepted=accepted and sd_pass
        resets=[]
        for index in range(args.soft_resets):
            if not accepted: break
            port.reset_input_buffer()
            port.write(b'!')
            port.flush()
            log=bytearray()
            deadline=time.monotonic()+args.timeout
            while time.monotonic()<deadline:
                chunk=port.read(4096)
                if chunk:
                    log.extend(chunk)
                    print(chunk.decode('utf-8',errors='replace'),end='',flush=True)
                if marker in log or b'DDR FAILED:' in log or b'RGB LCD FAIL:' in log: break
            good=marker in log and f'riscv-mini {args.stage.upper()} | RV32IM'.encode() in log
            if args.stage=='m4':
                good=good and b'MEM COPY PASS:' in log and b'MEM SOURCE FAIL' not in log and b'MEM COPY FAIL' not in log
            resets.append({'index':index+1,'passed':good,'uart':log.decode('utf-8',errors='replace')})
            accepted=accepted and good
        report={'stage':args.stage,'port':args.port,'baud':115200,'usb_location':args.location,
                'bitstream_sha256':validation['bitstream_sha256'],
                'programmer_exit_code':result.returncode,
                'startup':captured.decode('utf-8',errors='replace'),
                'echo':echo.decode('utf-8',errors='replace'),
                'acceptance_passed':accepted,'echo_verified':echo_ok,
                'soft_resets':resets,
                'sd_write_test_requested':args.sd_write_test,'sd_write_test_passed':sd_pass,
                'sd_test_uart':sd_log.decode('utf-8',errors='replace'),
                'reset_button_verified':False}
        (output/'uart.log').write_bytes(captured+echo+sd_log+b''.join(r['uart'].encode('utf-8') for r in resets))
        (output/'hardware-validation.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(f'\nAcceptance={accepted}, UART echo={echo_ok}',flush=True)
        if not accepted or not echo_ok: raise SystemExit(1)


if __name__=='__main__': main()
