"""Strict ICMP echo over the installed Windows Npcap driver."""
import ctypes as ct
import os
import json
import subprocess
from pathlib import Path
import socket
import struct
import time

def checksum(data):
    data += bytes(len(data)%2)
    total=sum(struct.unpack('!%dH'%(len(data)//2),data))
    while total>>16: total=(total&65535)+(total>>16)
    return (~total)&65535

def probe(interface_index,source_ip,destination_ip,destination_mac):
    adapter=json.loads(subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',
        'Get-NetAdapter -InterfaceIndex '+str(int(interface_index))+
        ' | Select-Object InterfaceGuid,MacAddress | ConvertTo-Json -Compress'],
        capture_output=True,text=True,check=True).stdout)
    device='\\Device\\NPF_'+adapter['InterfaceGuid']
    source_mac=adapter['MacAddress']
    directory=Path(os.environ['WINDIR'])/'System32/Npcap'

    dll_dir=os.add_dll_directory(str(directory))
    lib=ct.CDLL(str(directory/'wpcap.dll'))
    lib.pcap_open_live.argtypes=[ct.c_char_p,ct.c_int,ct.c_int,ct.c_int,ct.c_char_p]
    lib.pcap_open_live.restype=ct.c_void_p
    lib.pcap_setnonblock.argtypes=[ct.c_void_p,ct.c_int,ct.c_char_p]
    lib.pcap_sendpacket.argtypes=[ct.c_void_p,ct.c_char_p,ct.c_int]
    class Header(ct.Structure):
        _fields_=[('seconds',ct.c_long),('microseconds',ct.c_long),('captured',ct.c_uint),('length',ct.c_uint)]
    lib.pcap_next_ex.argtypes=[ct.c_void_p,ct.POINTER(ct.POINTER(Header)),ct.POINTER(ct.POINTER(ct.c_ubyte))]
    lib.pcap_close.argtypes=[ct.c_void_p]
    error=ct.create_string_buffer(256)
    handle=lib.pcap_open_live(device.encode(),65536,0,20,error)
    if not handle:raise RuntimeError(error.value.decode())
    source_mac=bytes.fromhex(source_mac.replace(':','').replace('-',''))
    destination_mac=bytes.fromhex(destination_mac.replace(':','').replace('-',''))
    source=socket.inet_aton(source_ip);destination=socket.inet_aton(destination_ip)
    results=[]
    try:
        if lib.pcap_setnonblock(handle,1,error):raise RuntimeError(error.value.decode())
        for sequence in range(4):
            payload=b'shared-writeback-icmp'+bytes([sequence])*12
            icmp=struct.pack('!BBHHH',8,0,0,0x524d,sequence)+payload
            icmp=icmp[:2]+struct.pack('!H',checksum(icmp))+icmp[4:]
            ip=struct.pack('!BBHHHBBH4s4s',0x45,0,20+len(icmp),sequence,0,64,1,0,source,destination)
            ip=ip[:10]+struct.pack('!H',checksum(ip))+ip[12:]
            frame=destination_mac+source_mac+b'\x08\x00'+ip+icmp
            started=time.monotonic()
            if lib.pcap_sendpacket(handle,frame,len(frame)):raise RuntimeError('pcap_sendpacket failed')
            deadline=started+2
            while time.monotonic()<deadline:
                header=ct.POINTER(Header)();packet=ct.POINTER(ct.c_ubyte)()
                code=lib.pcap_next_ex(handle,ct.byref(header),ct.byref(packet))
                if code<0:raise RuntimeError('pcap capture failed')
                if not code:time.sleep(.001);continue
                data=ct.string_at(packet,header.contents.captured)
                if len(data)<42 or data[:6]!=source_mac or data[6:12]!=destination_mac or data[12:14]!=b'\x08\x00':continue
                ip=data[14:];ihl=(ip[0]&15)*4;length=int.from_bytes(ip[2:4],'big')
                if ihl<20 or length>len(ip) or ip[9]!=1 or ip[12:16]!=destination or ip[16:20]!=source:continue
                reply=ip[ihl:length]
                if len(reply)<8 or reply[:2]!=b'\x00\x00' or reply[4:8]!=struct.pack('!HH',0x524d,sequence):continue
                if checksum(ip[:ihl]) or checksum(reply) or reply[8:]!=payload:raise RuntimeError('ICMP checksum or payload mismatch')
                results.append((time.monotonic()-started)*1000);break
            else:raise RuntimeError('Raw ICMP reply timeout')
        return {'packets':4,'replies':len(results),'rtt_ms':results,'transport':'Npcap Ethernet'}
    finally:
        lib.pcap_close(handle);dll_dir.close()
