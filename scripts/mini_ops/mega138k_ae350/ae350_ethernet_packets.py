"""Raw-wire ARP/ICMP/UDP checks on the explicitly selected direct interface.

No address, route, MAC or promiscuous-mode changes. The board test image owns
192.0.2.2/02:35:00:00:00:01; frames use 192.0.2.1 as their isolated test source.
"""
import json
import os
import random
import re
import socket
import struct
import time
from pathlib import Path

BOARD_MAC=bytes.fromhex('023500000001')
BOARD_IP=socket.inet_aton('192.0.2.2')
HOST_IP=socket.inet_aton('192.0.2.1')


def checksum(data):
    if len(data)&1:data+=b'\0'
    total=sum(struct.unpack('!%dH'%(len(data)//2),data))
    while total>>16:total=(total&0xffff)+(total>>16)
    return (~total)&0xffff


def ip_frame(mac,proto,body,sequence=0):
    header=struct.pack('!BBHHHBBH4s4s',0x45,0,20+len(body),sequence,0x4000,64,proto,0,HOST_IP,BOARD_IP)
    header=header[:10]+struct.pack('!H',checksum(header))+header[12:]
    return BOARD_MAC+mac+b'\x08\0'+header+body


def ipv4_reply(frame,mac,proto):
    if len(frame)<34 or frame[:6]!=mac or frame[6:12]!=BOARD_MAC or frame[12:14]!=b'\x08\0':return None
    packet=frame[14:];ihl=(packet[0]&15)*4;total=int.from_bytes(packet[2:4],'big')
    if packet[0]>>4!=4 or ihl<20 or ihl>60 or total<ihl or total>len(packet):return None
    if packet[9]!=proto or packet[12:16]!=BOARD_IP or packet[16:20]!=HOST_IP:return None
    if checksum(packet[:ihl]) or int.from_bytes(packet[6:8],'big')&0x3fff:return None
    return packet[ihl:total]


def exercise(interface,directory):
    if os.name!='posix' or not hasattr(socket,'AF_PACKET'):raise RuntimeError('Raw Ethernet tests require Linux')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}',interface):raise ValueError('Invalid interface name')
    root=Path('/sys/class/net')/interface
    mac=bytes.fromhex((root/'address').read_text().strip().replace(':',''))
    if (root/'carrier').read_text().strip()!='1':raise RuntimeError('Selected direct interface has no carrier')
    reports=[];rng=random.Random(0xae350)
    sock=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(3))
    sock.bind((interface,0));sock.settimeout(.1)
    def exchange(name,frame,predicate,quiet=False):
        last=None;started=time.monotonic()
        for attempt in range(1 if quiet else 3):
            sock.send(frame);deadline=time.monotonic()+(.3 if quiet else 1)
            while time.monotonic()<deadline:
                try:packet=sock.recv(4096)
                except socket.timeout:continue
                if len(packet)<14 or packet[6:12]!=BOARD_MAC:continue
                if predicate(packet):
                    last=packet;break
            if last is not None:break
        passed=(last is None) if quiet else (last is not None)
        item=dict(name=name,passed=passed,attempts=attempt+1,tx=frame.hex(),rx=None if last is None else last.hex(),
                  elapsed_ms=round((time.monotonic()-started)*1000,3))
        reports.append(item)
        if not passed:raise RuntimeError('Ethernet wire check failed: '+name)
    try:
        arp=b'\xff'*6+mac+b'\x08\x06'+struct.pack('!HHBBH',1,0x800,6,4,1)+mac+HOST_IP+b'\0'*6+BOARD_IP
        def arp_ok(p):
            return len(p)>=42 and p[:6]==mac and p[12:14]==b'\x08\x06' and p[20:22]==b'\0\2' and p[22:28]==BOARD_MAC and p[28:32]==BOARD_IP and p[32:38]==mac and p[38:42]==HOST_IP
        exchange('arp',arp,arp_ok)
        for seq,n in enumerate((0,1,31,56,257,1000,1472)):
            payload=bytes(rng.randrange(256) for _ in range(n))
            body=struct.pack('!BBHHH',8,0,0,0x3501,seq)+payload
            body=body[:2]+struct.pack('!H',checksum(body))+body[4:]
            def icmp_ok(frame,payload=payload,seq=seq):
                b=ipv4_reply(frame,mac,1)
                return b is not None and len(b)==8+len(payload) and b[:2]==b'\0\0' and checksum(b)==0 and b[4:8]==struct.pack('!HH',0x3501,seq) and b[8:]==payload
            exchange('icmp-%d'%n,ip_frame(mac,1,body,seq),icmp_ok)
        last_frame=last_predicate=None
        lengths=[0,1,31,64,511,1024,1472]+[rng.randrange(1473) for _ in range(32)]
        for seq,n in enumerate(lengths):
            payload=struct.pack('!I',seq)+bytes(rng.randrange(256) for _ in range(max(0,n-4))) if n>=4 else bytes(range(n))
            body=struct.pack('!HHHH',54321,32768,8+len(payload),0)+payload
            pseudo=HOST_IP+BOARD_IP+struct.pack('!BBH',0,17,len(body))
            c=checksum(pseudo+body);body=body[:6]+struct.pack('!H',c or 0xffff)+body[8:]
            def udp_ok(frame,payload=payload):
                b=ipv4_reply(frame,mac,17)
                if b is None or len(b)!=8+len(payload):return False
                if struct.unpack('!HHH',b[:6])!=(32768,54321,len(b)) or b[8:]!=payload:return False
                return b[6:8]!=b'\0\0' and checksum(BOARD_IP+HOST_IP+struct.pack('!BBH',0,17,len(b))+b)==0
            frame=ip_frame(mac,17,body,seq)
            exchange('udp-%d-%d'%(seq,n),frame,udp_ok);last_frame,last_predicate=frame,udp_ok
        for name,ident,offset in (('bad-ip-checksum',0xf001,24),('bad-udp-checksum',0xf002,40)):
            bad=bytearray(last_frame);bad[18:20]=struct.pack('!H',ident)
            bad[24:26]=b'\0\0';bad[24:26]=struct.pack('!H',checksum(bytes(bad[14:34])))
            bad[offset]^=1
            def unexpected_reply(frame,ident=ident,payload=payload):
                b=ipv4_reply(frame,mac,17)
                return b is not None and frame[18:20]==struct.pack('!H',ident) and b[8:]==payload
            exchange(name,bytes(bad),unexpected_reply,quiet=True)
        return dict(interface=interface,host_mac=mac.hex(':'),board_mac=BOARD_MAC.hex(':'),
                    arp_passed=True,icmp_cases=7,udp_cases=len(lengths),bad_checksum_cases=2,checks=len(reports),
                    retries=sum(item['attempts']-1 for item in reports),passed=True)
    finally:
        sock.close()
        (Path(directory)/'wire-packets.json').write_text(json.dumps(reports,indent=2)+'\n')
