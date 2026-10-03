"""Exercise existing monitor commands with line-in and real network traffic.

The firmware owns tests; this client supplies external measurements only.
Connect to an already running monitor. No reset, Flash write or NIC change.
"""
import argparse
import ctypes
import hashlib
import json
import re
import socket
import struct
import subprocess
import time
from pathlib import Path
import serial
from audio_capture import record, analyze
from boot_upload import BootSession, verified_output


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--port',default='COM4')
    p.add_argument('--audio',action='store_true')
    p.add_argument('--device',default='Line In')
    p.add_argument('--host-ip')
    p.add_argument('--interface-index',type=int)
    p.add_argument('--seconds',type=int,default=30)
    a=p.parse_args()
    if bool(a.host_ip)!=bool(a.interface_index):p.error('Supply both host IP and interface index')
    if not 0<=a.seconds<=300:p.error('Use 0..300 seconds')
    output=a.output_dir.resolve();validation,_=verified_output(output)
    features=validation.get('features',{})
    def enabled(name):return features.get(name,True)
    if a.audio and not enabled('audio'):p.error('--audio requires audio')
    if a.host_ip and not enabled('eth'):p.error('Network measurements require Ethernet')
    report={'passed':False,'bitstream_sha256':validation['bitstream_sha256'],
        'image_sha256':hashlib.sha256((output/'firmware/app.img').read_bytes()).hexdigest(),
        'commands':[],'excluded':'Physical input and screen observation; no Flash or power-cycle test'}
    try:
        with serial.Serial(a.port,115200,timeout=.05) as port,(output/'external-verification-uart.log').open('wb') as log:
            port.reset_input_buffer();session=BootSession(port,log)
            def command(name):
                port.write((name+'\r').encode());port.flush();raw=session.until(b'> ',120)
                text=raw.decode(errors='replace');report['commands'].append({'name':name,'output':text})
                if name.startswith('test ') and ('TEST '+name[5:]+' PASS') not in text:
                    raise RuntimeError(text)
                return text
            def network_echo(udp,length,index):
                payload=bytes((i*37+index*11)&255 for i in range(length));start=time.monotonic()
                udp.sendto(payload,('169.254.20.20',1234));reply,peer=udp.recvfrom(2048)
                if reply!=payload or peer!=('169.254.20.20',1234):raise RuntimeError('UDP payload/peer mismatch')
                return (time.monotonic()-start)*1000
            initial=command('status')
            identity=re.search(r'MAC=([0-9A-F:]{17})',initial)
            try:
                if a.audio:
                    command('test audio start');wav=output/'audio-line-in.wav'
                    device=record(wav,device_name=a.device,on_start=lambda:command('test audio tone'))
                    capture=analyze(wav,validation['audio_sample_rate']);capture['device']=device
                    report['audio']=capture
                    for ch in capture['channels']:
                        if abs(ch['peak_hz']-ch['expected_hz'])>3 or ch['tone_amplitude']<.0001 or ch['separation_db']<12 or ch['clipped_samples']:
                            raise RuntimeError('Line-in frequency/amplitude/channel separation failed')
                        if ch['mute_tone_attenuation_db']<40:raise RuntimeError('Audio auto-mute failed')
                    command('test audio stop');print('External stereo line-in PASS',flush=True)
                if a.host_ip:
                    report.update(host_ip=a.host_ip,interface_index=a.interface_index)
                    command('test eth start')
                    send_arp=ctypes.windll.iphlpapi.SendARP
                    send_arp.argtypes=[ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
                    send_arp.restype=ctypes.c_uint32
                    mac=(ctypes.c_ubyte*8)();length=ctypes.c_uint32(8)
                    code=send_arp(struct.unpack('<I',socket.inet_aton('169.254.20.20'))[0],
                                  struct.unpack('<I',socket.inet_aton(a.host_ip))[0],mac,ctypes.byref(length))
                    if code:raise RuntimeError(f'ARP failed: Windows error {code}')
                    report['arp_mac']=bytes(mac[:length.value]).hex(':')
                    if not identity or report['arp_mac']!=identity[1].lower():raise RuntimeError('ARP MAC mismatch')
                    ping=subprocess.run(['ping','-S',a.host_ip,'-n','4','-w','2000','169.254.20.20'],capture_output=True)
                    report['ping']=ping.stdout.decode(errors='replace')
                    if ping.returncode:raise RuntimeError('ICMP ping failed')
                    if enabled('audio'):command('test audio start')
                    sizes=[0,1,31,32,63,64,255,511,1024,1472]
                    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
                        udp.setsockopt(socket.IPPROTO_IP,31,struct.pack('!I',a.interface_index))
                        udp.bind((a.host_ip,0));udp.settimeout(2)
                        count=total=0;latencies=[];started=time.monotonic();rounds=0
                        while True:
                            for size in sizes:
                                latencies.append(network_echo(udp,size,count));count+=1;total+=size
                            for name,feature in [('usb','usb'),('sd','filesystem'),('lcd','video'),('eth','eth')]:
                                if enabled(feature):command('test '+name)
                            rounds+=1
                            if time.monotonic()-started>=a.seconds:break
                        report['network']={'udp_packets':count,'payload_bytes':total,'udp_timeouts':0,
                            'max_rtt_ms':max(latencies),'seconds':time.monotonic()-started,'rounds':rounds,'sizes':sizes}
                    print('External Ethernet/concurrency PASS '+json.dumps(report['network']),flush=True)
                final=command('status')
                audio=re.search(r'AUDIO [^\r\n]+',final)
                if audio and ('underruns=00000000' not in audio[0] or 'errors=00000000' not in audio[0]):
                    raise RuntimeError('Audio errors during external tests')
                if enabled('video') and 'underflows=00000000' not in final:raise RuntimeError('LCD underflow during external tests')
                report['passed']=True
            finally:
                if enabled('audio'):command('test audio stop')
                if a.host_ip:command('test eth stop')
    except Exception as error:
        report['passed']=False;report['error']=str(error);raise
    finally:
        (output/'external-verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
