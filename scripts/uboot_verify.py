"""Boot the U-Boot OSB1 payload via BIOS TFTP and check the console.

This is the reusable U-Boot net-boot harness (scripts/*_verify.py convention).
It composes the only primitives that exist: reset the FPGA (SRAM), reach the
resident BIOS, serve the payload over TFTP in-process, `boot net`, then type
commands at the U-Boot prompt. Prefer extending this over writing one-off
build/*.py scripts. --tftp-stats prints per-block DATA->ACK latency from the
shared PayloadServer so transfer timing needs no bespoke server."""
import argparse,concurrent.futures,sys,time
from pathlib import Path
import serial
from bios_tftp import PayloadServer
from boot_upload import BootSession, program
from bios_image import unpack,OS_MAGIC

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image',type=Path,default=Path('build/uboot/firmware/UBOOT.OSB'))
    p.add_argument('--app',type=Path,help='BIOS app.img to UART-load first (with --program)')
    p.add_argument('--port',default='COM4')
    p.add_argument('--host-ip',default='169.254.25.153')
    p.add_argument('--timeout',type=float,default=90)
    p.add_argument('--cmd',action='append',default=[],help='Command typed at the U-Boot prompt (repeatable)')
    p.add_argument('--log',type=Path,default=Path('build/uboot/verify-uart.log'))
    p.add_argument('--no-tftp',action='store_true',help='Serve externally (firewall-allowed interpreter); do not bind UDP/69 here')
    p.add_argument('--program',action='store_true',help='Reset the FPGA (SRAM reprogram) before connecting')
    p.add_argument('--soc-dir',type=Path,default=Path('build/runs/v0.7.1-ce277e099275-full-rv32imafc-rom4k-l24k-final-build'))
    p.add_argument('--location',default='107569')
    p.add_argument('--tftp-stats',action='store_true',help='Print per-block DATA->ACK latency after the transfer')
    a=p.parse_args();data=a.image.read_bytes();h,_=unpack(data)
    if h[0]!=OS_MAGIC:raise ValueError('Expected OSB1')
    name=a.image.name
    if not a.cmd:a.cmd=['version']
    if a.program:program(a.soc_dir.resolve(),a.location)
    a.log.parent.mkdir(parents=True,exist_ok=True)
    with a.log.open('wb') as log,serial.Serial(a.port,115200,timeout=.1) as port:
        session=BootSession(port,log)
        def command(s):
            port.write((s+'\r').encode());return session.until(b'> ',30)
        if a.program and a.app:
            # Fresh SRAM reset: enter the loader menu and UART-load the BIOS app.
            session.menu(reset=True);session.upload(a.app.read_bytes())
        else:
            # Reach the resident BIOS prompt: from a stuck payload '!' resets via BIOS NMI;
            # from the BL> loader menu, 'b' re-arms BOOT SELECT and Flash auto-boot enters BIOS.
            port.write(b'!');port.flush()
            try:
                got=session.until(b'> ',8)
                if b'BL> ' in got:
                    port.write(b'b');port.flush()
                    session.until(b'BOOT SELECT:',10)
                    got=session.until(b'> ',10)  # Flash auto in 2s boots the BIOS
                print(got.decode(errors='replace'),flush=True)
            except TimeoutError:
                raise RuntimeError('No BIOS prompt after reset request; re-program SRAM (boot_upload --program) and rerun')
        print(command('set server '+a.host_ip).decode(errors='replace'),flush=True)
        server=PayloadServer(a.host_ip,data,name) if not a.no_tftp else None
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(server.serve_once) if server else None
                port.write(('boot net '+name+'\r').encode())
                result=bytearray();deadline=time.monotonic()+a.timeout
                while time.monotonic()<deadline:
                    chunk=port.read(port.in_waiting or 1)
                    if chunk:
                        result.extend(chunk);session.note(chunk)
                        print(chunk.decode(errors='replace'),end='',flush=True)
                        if b'riscv-mini> ' in result:break
                        for fail in (b'TFTP unavailable',b'failed',b'ERR '):
                            if fail in result:
                                raise RuntimeError('Board boot failed: '+result.decode(errors='replace').strip()[-400:])
                else:raise TimeoutError('U-Boot prompt timed out; see UART log')
                if future and not future.result():raise AssertionError('TFTP failed')
                if b'U-Boot 20' not in result:raise AssertionError('U-Boot banner missing')
                for c in a.cmd:
                    port.write((c+'\r').encode())
                    reply=session.until(b'riscv-mini> ',15)
                    result.extend(reply);print(reply.decode(errors='replace'),flush=True)
                if a.tftp_stats and server and server.stats:
                    s=server.stats;lat=sorted(s['ack_ms']);n=len(lat)
                    print(f"\n=== TFTP stats ===\nblocks={s['blocks']} sent={s['sent']} retries={s['retries']} total={s['total_s']:.2f}s -> {len(data)/s['total_s']/1024:.1f} KiB/s",flush=True)
                    if n:
                        pct=lambda q:lat[min(n-1,int(n*q))]
                        print(f"DATA->ACK ms: min={lat[0]:.1f} p50={pct(.5):.1f} p90={pct(.9):.1f} p99={pct(.99):.1f} max={lat[-1]:.1f}",flush=True)
                print('UBOOT CONSOLE PASS',flush=True)
        finally:
            if server:server.close()
if __name__=='__main__':main()
