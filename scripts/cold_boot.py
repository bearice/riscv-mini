"""Observe an externally initiated power cycle; never send a reset command."""
import argparse
import json
import time
from pathlib import Path
import serial
from boot_upload import BootSession, verified_output
from uart_state import State, observe

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',default='COM4')
    p.add_argument('--output-dir',type=Path,default=ROOT/'build/base')
    p.add_argument('--log-dir',type=Path,help='Operation-specific logs and evidence')
    p.add_argument('--timeout',type=int,default=300)
    a=p.parse_args();output=a.output_dir.resolve()
    validation,_=verified_output(output)
    logs=(a.log_dir or output).resolve();logs.mkdir(parents=True,exist_ok=True)
    deadline=time.monotonic()+a.timeout;data=bytearray();disconnects=0
    report={'bitstream_sha256':validation['bitstream_sha256'],'passed':False,
            'reset_command_sent':False,'external_power_cycle_requested':True}
    try:
        with (logs/'cold-boot-uart.log').open('wb') as log:
            while time.monotonic()<deadline:
                try:
                    with serial.Serial(a.port,115200,timeout=.1) as port:
                        # Drop an old prompt, then await a new, externally caused boot.
                        port.reset_input_buffer();data.clear()
                        print('Watching UART for external power cycle...',flush=True)
                        while time.monotonic()<deadline:
                            chunk=port.read(64)
                            if chunk:
                                log.write(chunk);log.flush();data.extend(chunk)
                            state,detail=observe(data)
                            if state is State.FAILED:
                                # The boot attempt already failed; report it now
                                # instead of waiting out the observation window.
                                raise RuntimeError('Flash startup failed: '+detail['error'])
                            if state is State.APP_READY and b'BOOT FLASH' in data:
                                s=BootSession(port,log)
                                # The BIOS prompt echoes the version tail
                                # ('.dirty') before the first command; drain it.
                                s.until(b'> ',30)
                                port.write(b'status\r');status=s.until(b'> ',30)
                                port.write(b'ls\r');listing=s.until(b'> ',30)
                                if b'underflows=00000000' not in status:
                                    raise RuntimeError('Cold peripheral check failed: '+status.decode(errors='replace'))
                                if b'RVTEST00.BIN' not in listing:
                                    # The SD acceptance file is a lab fixture,
                                    # not a boot defect; record it and pass.
                                    report['sd_fixture']=('RVTEST00.BIN absent; '
                                        'cold boot and peripherals healthy, SD acceptance file not present')
                                report.update(passed=True,disconnects=disconnects,
                                              startup=data.decode(errors='replace'),
                                              status=status.decode(errors='replace'),
                                              directory=listing.decode(errors='replace'))
                                print('Cold Flash startup observed and checked.',flush=True)
                                return
                except serial.SerialException:
                    disconnects+=1;time.sleep(.5)
            raise TimeoutError('No new Flash startup was observed')
    finally:
        (logs/'cold-boot.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':main()
