"""Run the OpenSBI probe from BIOS over an existing isolated Ethernet link."""
import argparse,concurrent.futures,sys,time
from pathlib import Path
import serial
from bios_tftp import PayloadServer
from boot_upload import BootSession
from bios_image import unpack,OS_MAGIC

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image',type=Path,default=Path('build/opensbi/firmware/OPENSBI.OSB'))
    p.add_argument('--reset-bios',type=Path,help='After PASS test SBI warm reset and UART-reload this BIOS image')
    storage=p.add_mutually_exclusive_group()
    storage.add_argument('--sd',action='store_true',help='Fetch a new SBI1.OSB file then boot from SD')
    storage.add_argument('--sd-existing',help='Boot this existing SD file without network or card writes')
    p.add_argument('--timeout',type=float,default=120)
    p.add_argument('--port',default='COM4');p.add_argument('--host-ip',default='169.254.25.153')
    p.add_argument('--log',type=Path,default=Path('build/opensbi/probe-uart.log'))
    a=p.parse_args();data=a.image.read_bytes();h,_=unpack(data)
    if h[0]!=OS_MAGIC:raise ValueError('Expected OSB1')
    a.log.parent.mkdir(parents=True,exist_ok=True)
    with a.log.open('wb') as log,serial.Serial(a.port,115200,timeout=.1) as port:
        session=BootSession(port,log)
        def command(s):
            port.write((s+'\r').encode());return session.until(b'> ',30)
        command('')
        if not a.sd_existing:print(command('set server '+a.host_ip).decode(errors='replace'),flush=True)
        name=a.sd_existing or ('SBI1.OSB' if a.sd else 'OPENSBI.OSB')
        server=PayloadServer(a.host_ip,data,name) if not a.sd_existing else None
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(server.serve_once) if server else None
                if a.sd:
                    reply=command('fetch '+name);print(reply.decode(errors='replace'),flush=True)
                    if b'FETCH SAVED '+name.encode() not in reply:raise AssertionError('SD fetch failed (file must be new)')
                    if not future.result():raise AssertionError('TFTP failed')
                port.write(('boot sd '+name+'\r' if a.sd or a.sd_existing else 'boot net '+name+'\r').encode())
                result=bytearray();deadline=time.monotonic()+a.timeout
                marker=b'OPENSBI PROBE PASS\r\n'
                while time.monotonic()<deadline:
                    chunk=port.read(port.in_waiting or 1)
                    if chunk:
                        result.extend(chunk);session.note(chunk)
                        print(chunk.decode(errors='replace'),end='',flush=True)
                        if marker in result:break
                else:raise TimeoutError('OpenSBI probe timed out; see UART log')
                if future and not future.result():raise AssertionError('TFTP failed')
                required=[b'SBI BASE PASS',b'TIME CSR PASS',b'ATOMIC PASS',b'S TIMER PASS',b'S EXTERNAL PASS',b'U ECALL / TIME PASS',b'USER MMU kernel/MMIO/RX PASS']
                if any(s not in result for s in required):raise AssertionError('Missing probe result')
                if a.reset_bios:
                    port.write(b'r');reset=session.menu();print(reset.decode(errors='replace'),flush=True)
                    if b'SBI RESET REQUEST' not in reset:raise AssertionError('Reset command not executed')
                    print(session.upload(a.reset_bios.read_bytes()).decode(errors='replace'),flush=True)
                    print('SBI RESET / BIOS RECOVERY PASS',flush=True)
        finally:
            if server:server.close()
if __name__=='__main__':main()
