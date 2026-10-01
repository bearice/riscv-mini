"""Direct-PC Ethernet demo: ARP/ping/UDP, shared reset and <=5m media coexistence.

Bind the host's existing IPv4 address and interface; never change NIC configuration.
"""
import argparse
import ctypes
import json
import re
import socket
import struct
import subprocess
import time
from pathlib import Path
import serial
from boot_image import unpack_image
from boot_upload import BootSession, verified_output, program
ROOT=Path(__file__).resolve().parents[1]

def fields(text,prefix):
    line=re.search(prefix+rb'([^\r]+)',text)
    if not line:raise RuntimeError('Missing status '+repr(prefix))
    return {key.decode():int(value,16) for key,value in re.findall(rb'(\w+)=([0-9a-f]{8})',line[0])}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,default=ROOT/'build/m8')
    p.add_argument('--image',type=Path,default=ROOT/'build/m8-demo/firmware/app.img')
    p.add_argument('--port',default='COM4');p.add_argument('--location',default='107569')
    p.add_argument('--host-ip',required=True);p.add_argument('--interface-index',type=int,required=True)
    p.add_argument('--board-ip',default='169.254.20.20')
    p.add_argument('--program',action='store_true');p.add_argument('--reset',action='store_true')
    p.add_argument('--soak-seconds',type=float,default=300)
    p.add_argument('--settle-seconds',type=float,default=0,
        help='Explicit quiet interval after shared PHY reset, before steady-state soak (0..30s)')
    p.add_argument('--usb',action='store_true',help='M9: check connected HID receiver, USB-only/shared reset and OHCI frames')
    args=p.parse_args()
    if not 0<=args.soak_seconds<=300:p.error('Use 0..300 seconds')
    if not 0<=args.settle_seconds<=30:p.error('Use 0..30 settling seconds')
    output=args.output_dir.resolve();validation,_=verified_output(output);image=args.image.read_bytes()
    unpack_image(image,validation['boot_image']['abi_tag'])
    report={'passed':False,'checks':{},'host_ip':args.host_ip,'interface_index':args.interface_index,
            'board_ip':args.board_ip,'bitstream_sha256':validation['bitstream_sha256']}
    try:
        with (output/'ethernet-verification-uart.log').open('wb') as log,serial.Serial(args.port,115200,timeout=.05) as port:
            port.reset_input_buffer()
            if args.program:program(output,args.location)
            session=BootSession(port,log);session.menu(args.reset);startup=session.upload(image)
            report['checks']['startup']=startup.decode(errors='replace')
            if b'NETWORK PARSER PASS' not in startup or b'ETHERNET DEMO' not in startup:raise RuntimeError(startup)
            identity=re.search(rb'MAC=([0-9a-f:]{17})',startup)
            uid=re.search(rb'FLASH UID=([0-9a-f]+)',startup)
            if not identity or not uid:raise RuntimeError('Missing Flash UID / MAC')
            board_mac=identity[1].decode();raw_uid=bytes.fromhex(uid[1].decode())
            hashed=14695981039346656037
            for value in raw_uid:hashed=((hashed^value)*1099511628211)&0xffffffffffffffff
            expected=bytearray(hashed.to_bytes(8,'little')[:6]);expected[0]=(expected[0]&0xfe)|2
            if board_mac!=expected.hex(':'):raise RuntimeError('UID-derived MAC mismatch')
            report['board_mac']=board_mac;report['flash_uid']=raw_uid.hex()
            print(startup.decode(errors='replace'),flush=True)
            def command(letter):
                port.write(letter.encode());port.flush();text=session.until(b'> ',30)
                if b' FAIL' in text:raise RuntimeError(text.decode(errors='replace'))
                return text
            def status(running=False):
                text=command('s');eth=fields(text,b'ETH ')
                if not eth['ready'] or eth['phy']&0xfffffff0!=0x001cc810 or eth['crc'] or eth['preamble'] or eth['mdio']:raise RuntimeError(text)
                if running:
                    audio=fields(text,b'AUDIO ')
                    if audio['control']!=7 or audio['underruns'] or audio['overruns'] or audio['errors'] or audio['amp']:raise RuntimeError(text)
                    if b'underflows=00000000' not in text or b'SD ready=00000001' not in text or b'errors=00000000 drops=00000000 unhandled=00000000' not in text:raise RuntimeError(text)
                return eth,text
            def usb_status():
                text=command('u');usb=fields(text,b'USB ready=')
                if not usb['ready'] or not usb['phy_ready'] or usb['phy']!=0x00060424 or usb['phy_error'] or usb['errors'] or usb['key_drops'] or usb['report_drops']:
                    raise RuntimeError('USB failure: '+text.decode(errors='replace'))
                if not 0x40800000<=usb['hcca']<0x40c00000 or usb['hcca']&255:raise RuntimeError('OHCI HCCA must be 256-byte aligned in application DDR')
                return usb,text
            def await_usb():
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    usb,text=usb_status()
                    if usb['connected'] and usb['hid']:return usb
                    time.sleep(.1)
                raise RuntimeError('No enumerated HID receiver: '+text.decode(errors='replace'))
            if args.usb:
                report['checks']['usb_initial']=await_usb()
                for i in range(3):
                    if b'USB RESTART PASS' not in command('j'):raise RuntimeError('USB local restart failed')
                    report['checks'][f'usb_restart_{i+1}']=await_usb()
            def await_link():
                deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    eth,text=status()
                    if eth['link'] and eth['mbps']==100 and eth['full']:return eth
                    time.sleep(.5)
                raise RuntimeError('No negotiated 100M full-duplex link: '+text.decode(errors='replace'))
            report['checks']['link']=await_link();print('100M full-duplex link ready',flush=True)
            def resolve_arp():
                # Explicitly resolve on the selected source interface. Following
                # PHY reset, Windows may temporarily retain an unreachable entry.
                send_arp=ctypes.windll.iphlpapi.SendARP
                send_arp.argtypes=[ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
                send_arp.restype=ctypes.c_uint32
                destination=struct.unpack('<I',socket.inet_aton(args.board_ip))[0]
                source=struct.unpack('<I',socket.inet_aton(args.host_ip))[0]
                deadline=time.monotonic()+20;code=0
                while time.monotonic()<deadline:
                    mac=(ctypes.c_ubyte*8)();length=ctypes.c_uint32(8)
                    code=send_arp(destination,source,mac,ctypes.byref(length))
                    if not code:
                        value=bytes(mac[:length.value]).hex(':')
                        if value!=board_mac:raise RuntimeError('Unexpected ARP MAC '+value)
                        return value
                    time.sleep(.25)
                raise RuntimeError(f'ARP resolution failed: Windows error {code}')
            report['checks']['arp']=resolve_arp()
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
                # Windows IP_UNICAST_IF uses a network-byte-order DWORD.
                udp.setsockopt(socket.IPPROTO_IP,31,struct.pack('!I',args.interface_index))
                udp.bind((args.host_ip,0));udp.settimeout(2)
                count=0;bytes_total=0;latencies=[]
                def echo(length):
                    nonlocal count,bytes_total
                    payload=bytes(((i*37+count*11)&255) for i in range(length));started=time.monotonic()
                    udp.sendto(payload,(args.board_ip,1234))
                    try:reply,peer=udp.recvfrom(2048)
                    except TimeoutError:
                        report['failure']={'reason':'UDP timeout','packet':count,'payload_length':length,
                            'elapsed_ms':(time.monotonic()-started)*1000}
                        # Capture the still-running board, without retrying the
                        # lost packet or turning a failure into a passing run.
                        report['failure']['status']=command('s').decode(errors='replace')
                        if args.usb:report['failure']['usb_status']=command('u').decode(errors='replace')
                        raise
                    if reply!=payload or peer!=(args.board_ip,1234):raise RuntimeError('UDP payload/peer mismatch')
                    count+=1;bytes_total+=length;latencies.append((time.monotonic()-started)*1000)
                sizes=[0,1,31,32,63,64,255,511,1024,1472]
                for size in sizes:echo(size)
                report['checks']['udp_sizes']=sizes
                ping=subprocess.run(['ping','-S',args.host_ip,'-n','4','-w','2000',args.board_ip],capture_output=True)
                report['checks']['ping']=ping.stdout.decode(errors='replace')
                if ping.returncode:raise RuntimeError('ICMP ping failed: '+repr(ping.stdout))
                # F10 reset intentionally resets both PHYs; no host NIC mutation.
                command('e');report['checks']['shared_reset']=await_link()
                if args.usb:report['checks']['usb_shared_reset']=await_usb()
                report['checks']['arp_after_reset']=resolve_arp()
                for size in sizes:echo(size)
                if args.settle_seconds:
                    report['settling']={'seconds':args.settle_seconds,'status_before':status()[1].decode(errors='replace'),
                        'scope':'No UDP test traffic during this explicit post-reset quiet interval; not part of soak'}
                    print(f'Explicit post-reset settling interval: {args.settle_seconds}s (outside soak)',flush=True)
                    deadline=time.monotonic()+args.settle_seconds
                    while time.monotonic()<deadline:time.sleep(min(.1,max(0,deadline-time.monotonic())))
                    report['settling']['status_after']=status()[1].decode(errors='replace')
                command('d');baseline=status(True)[0]['drops'];started=time.monotonic();rounds=0;first=None;last=None
                usb_before=await_usb() if args.usb else None
                while time.monotonic()-started<args.soak_seconds:
                    for size in sizes:echo(size)
                    if b'FRAME PASS' not in command('f'):raise RuntimeError('Frame swap missing')
                    if b'SD READ PASS bytes=00001000 crc=08040e1e' not in command('r'):raise RuntimeError('SD CRC mismatch')
                    eth,text=status(True)
                    if not eth['link']:raise RuntimeError('Link lost: '+text.decode(errors='replace'))
                    if args.usb:
                        usb,usb_text=usb_status()
                        if not usb['connected'] or not usb['hid']:raise RuntimeError('USB receiver lost: '+usb_text.decode(errors='replace'))
                        if rounds and usb['frame']==usb_previous['frame']:raise RuntimeError('OHCI frame clock stopped')
                        usb_previous=usb
                    audio=fields(text,b'AUDIO ');lcd=fields(text,b'LCD ')
                    now=(eth['rx'],eth['tx'],audio['played'],audio['fetched'],lcd['frames'],lcd['completed'])
                    if last is not None and any(a==b for a,b in zip(now,last)):raise RuntimeError('Counters stopped')
                    if first is None:first=now
                    last=now;rounds+=1
                    if rounds%10==0:print(f'{rounds} rounds {time.monotonic()-started:.1f}s: UDP/SD/LCD/audio OK',flush=True)
                    # Keep packets flowing during the rest of each one-second round.
                    until=min(started+args.soak_seconds,started+rounds)
                    while time.monotonic()<until:echo(511);time.sleep(.02)
                report['soak']={'seconds':time.monotonic()-started,'rounds':rounds,'first':first,'last':last,
                    'udp_packets':count,'udp_timeouts':0,'payload_bytes':bytes_total,'max_rtt_ms':max(latencies),
                    'raw_rx_drops_before':baseline,'raw_rx_drops_after':status()[0]['drops']}
                report['checks']['final_status']=status(bool(args.soak_seconds))[1].decode(errors='replace')
                if args.usb:report['checks']['usb_soak']={'before':usb_before,'after':usb_status()[0]}
                command('x')
                report['passed']=True
    finally:
        (output/'ethernet-verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('Ethernet verification passed: '+json.dumps(report.get('soak',{})),flush=True)

if __name__=='__main__':main()
