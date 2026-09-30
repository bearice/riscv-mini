"""Observe an externally initiated power cycle; never send a reset command."""
import argparse
import json
import time
from pathlib import Path
import serial
from boot_upload import BootSession, verified_output

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',default='COM4')
    p.add_argument('--output-dir',type=Path,default=ROOT/'build/base')
    p.add_argument('--timeout',type=int,default=300)
    a=p.parse_args();output=a.output_dir.resolve()
    validation,_=verified_output(output)
    deadline=time.monotonic()+a.timeout;data=bytearray();disconnects=0
    report={'bitstream_sha256':validation['bitstream_sha256'],'passed':False,
            'reset_command_sent':False,'external_power_cycle_requested':True}
    try:
        with (output/'cold-boot-uart.log').open('wb') as log:
            while time.monotonic()<deadline:
                try:
                    with serial.Serial(a.port,115200,timeout=.1) as port:
                        # Drop an old prompt, then await a new, externally caused boot.
                        port.reset_input_buffer();data.clear()
                        print('Watching UART for external power cycle...',flush=True)
                        while time.monotonic()<deadline:
                            chunk=port.read(1)
                            if chunk:
                                log.write(chunk);log.flush();data.extend(chunk)
                            if data.endswith(b'> ') and b'BOOT FLASH' in data:
                                wanted=b'SYSTEM READY sd=00000001 spi_lcd=00000001 rgb_lcd=00000001'
                                if wanted not in data or b'ERR ' in data:raise RuntimeError('Cold startup failed')
                                s=BootSession(port,log)
                                port.write(b'status\r');status=s.until(b'> ',30)
                                port.write(b'ls\r');listing=s.until(b'> ',30)
                                if b'underflows=00000000' not in status or b'RVTEST00.BIN' not in listing:
                                    raise RuntimeError('Cold peripheral check failed')
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
        (output/'cold-boot.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':main()
