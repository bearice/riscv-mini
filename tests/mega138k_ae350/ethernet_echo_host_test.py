"""Native protocol tests: real reply bytes, malformed input and capacity guards."""
import ctypes as c
import os
import struct
import subprocess
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from mini_ops.common import Operation
from modular.storage import file_hash
from mini_ops.mega138k_ae350.ae350_ethernet_packets import checksum,ip_frame,ipv4_reply,BOARD_MAC,BOARD_IP,HOST_IP
HOST_MAC=bytes.fromhex('60beb409b5f5')
OP=Operation('ae350-ethernet-echo-host')
OP.report.update(kind='test',scope='native IPv4 echo protocol/capacity and MDIO read-framing tests')
SOURCE=ROOT/'firmware/bsp/mega138k_ae350/network/ethernet_echo.c'

class Endpoint(c.Structure):
    _fields_=[('mac',c.c_uint8*6),('ipv4',c.c_uint8*4),('udp_port',c.c_uint16)]

class EchoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        target=OP.path/'echo.so'
        command=[os.environ.get('CC','cc'),'-std=c11','-shared','-fPIC','-Wall','-Wextra','-Werror',str(SOURCE),'-o',str(target)]
        build=subprocess.run(command,capture_output=True,text=True)
        (OP.path/'compile.log').write_text(build.stdout+build.stderr)
        OP.record('compile',command=command,source_sha256=file_hash(SOURCE),returncode=build.returncode)
        build.check_returncode()
        cls.lib=c.CDLL(str(target));cls.call=cls.lib.ethernet_echo
        cls.call.argtypes=[c.POINTER(Endpoint),c.c_void_p,c.c_uint,c.c_void_p,c.c_uint,c.POINTER(c.c_uint)]
        cls.call.restype=c.c_int
        cls.endpoint=Endpoint();cls.endpoint.mac[:]=BOARD_MAC;cls.endpoint.ipv4[:]=BOARD_IP;cls.endpoint.udp_port=32768
    def reply(self,frame,capacity=1600):
        source=c.create_string_buffer(frame or b'\0')
        buf=(c.c_uint8*(capacity+32))(*([0xa5]*(capacity+32)));kind=c.c_uint(99)
        n=self.call(c.byref(self.endpoint),source,len(frame),c.byref(buf,16),capacity,c.byref(kind))
        self.assertEqual(bytes(buf[:16]),b'\xa5'*16)
        self.assertEqual(bytes(buf[16+capacity:]),b'\xa5'*16)
        if n<=0:self.assertEqual(bytes(buf),b'\xa5'*(capacity+32))
        self.assertLessEqual(n,capacity)
        return n,bytes(buf[16:16+max(0,n)]),kind.value
    def udp(self,payload):
        body=struct.pack('!HHHH',54321,32768,8+len(payload),0)+payload
        value=checksum(HOST_IP+BOARD_IP+struct.pack('!BBH',0,17,len(body))+body)
        body=body[:6]+struct.pack('!H',value or 0xffff)+body[8:]
        return ip_frame(HOST_MAC,17,body,77)
    def test_udp_lengths_and_reply_checksum(self):
        for length in (0,1,31,1472):
            data=bytes((i*37)&255 for i in range(length));n,p,kind=self.reply(self.udp(data))
            self.assertEqual(n,42+length);self.assertEqual(kind,3)
            body=ipv4_reply(p,HOST_MAC,17);self.assertIsNotNone(body)
            self.assertEqual(struct.unpack('!HHH',body[:6]),(32768,54321,8+length))
            self.assertEqual(body[8:],data)
            self.assertEqual(checksum(BOARD_IP+HOST_IP+struct.pack('!BBH',0,17,len(body))+body),0)
    def test_icmp_reply_bytes(self):
        data=b'odd payload';body=struct.pack('!BBHHH',8,0,0,0x3501,5)+data
        body=body[:2]+struct.pack('!H',checksum(body))+body[4:]
        n,p,kind=self.reply(ip_frame(HOST_MAC,1,body));self.assertEqual(kind,2)
        body=ipv4_reply(p,HOST_MAC,1);self.assertEqual(n,42+len(data))
        self.assertEqual(body[:2],b'\0\0');self.assertEqual(checksum(body),0)
        self.assertEqual(body[4:],struct.pack('!HH',0x3501,5)+data)
    def test_arp(self):
        frame=b'\xff'*6+HOST_MAC+b'\x08\x06'+struct.pack('!HHBBH',1,0x800,6,4,1)+HOST_MAC+HOST_IP+b'\0'*6+BOARD_IP
        n,p,kind=self.reply(frame);self.assertEqual((n,kind),(42,1))
        self.assertEqual(p[:12],HOST_MAC+BOARD_MAC);self.assertEqual(p[20:22],b'\0\2')
        self.assertEqual(p[22:42],BOARD_MAC+BOARD_IP+HOST_MAC+HOST_IP)
        for length in range(42):self.assertEqual(self.reply(frame[:length])[0],0)
    def test_bad_checksums_and_lengths(self):
        original=self.udp(b'hello')
        for offset in (24,40):
            frame=bytearray(original);frame[offset]^=1
            self.assertEqual(self.reply(bytes(frame))[0],-1)
        for length in range(len(original)):
            self.assertEqual(self.reply(original[:length])[0],0)
        frame=bytearray(original);frame[38:40]=b'\xff\xff'
        self.assertEqual(self.reply(bytes(frame))[0],0)
    def test_output_capacity(self):
        frame=self.udp(bytes(range(100)))
        for cap in (0,13,len(frame)-1):self.assertEqual(self.reply(frame,cap)[0],-2)
    def test_other_mac_is_ignored(self):
        frame=bytearray(self.udp(b'x'));frame[0]^=2
        self.assertEqual(self.reply(bytes(frame))[0],0)

class MdioReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        diag=ROOT/'firmware/diagnostics/mega138k_ae350/ethernet/main.c'
        driver=ROOT/'firmware/bsp/mega138k_ae350/network/ethernet_phy.c'
        def function(path,name):
            text=path.read_text();start=text.index(name);brace=text.index('{',start);depth=1;end=brace+1
            while depth:
                depth+=(text[end]=='{')-(text[end]=='}');end+=1
            return text[start:end]
        fixture=r'''
#include <stdint.h>
static unsigned reply[18],position,sent,write_bits,expected_phy,expected_reg,fault;
static unsigned bit(unsigned value) {
    if(value!=1)fault=1;
    unsigned i=position++;if(i>=18){fault=1;return 1;}return reply[i];
}
static void send(uint32_t value,unsigned bits) {
    if(sent==0) {if(bits!=32||value!=0xffffffffu)fault=1;}
    else if(sent==1) {if(bits!=14||value!=((6u<<10)|(expected_phy<<5)|expected_reg))fault=1;}
    else fault=1;
    sent++;write_bits+=bits;
}
'''
        fixture+=function(diag,'static int read_reg(')+'\n'
        fixture+=function(driver,'int ethernet_mdio_read(')+'\n'
        fixture+=r'''
int fixture_probe(unsigned variant,unsigned phy,unsigned reg,unsigned value,unsigned ack,
                  uint16_t *out,unsigned *clocks,unsigned *bad) {
    position=sent=write_bits=fault=0;expected_phy=phy;expected_reg=reg;reply[0]=ack;
    for(unsigned i=0;i<16;i++)reply[i+1]=(value>>(15-i))&1u;
    reply[17]=1;
    int result=variant?ethernet_mdio_read(phy,reg,out):read_reg(phy,reg,out);
    if(variant&&(phy>31||reg>31)) {if(sent||position)fault=1;}
    else if(sent!=2)fault=1;
    *clocks=write_bits+position;*bad=fault;return result;
}
'''
        source=OP.path/'mdio-fixture.c';source.write_text(fixture);target=OP.path/'mdio.so'
        command=[os.environ.get('CC','cc'),'-std=c11','-shared','-fPIC','-Wall','-Wextra','-Werror',str(source),'-o',str(target)]
        build=subprocess.run(command,capture_output=True,text=True)
        (OP.path/'mdio-compile.log').write_text(build.stdout+build.stderr);build.check_returncode()
        OP.record('mdio-fixture',diagnostic_sha256=file_hash(diag),driver_sha256=file_hash(driver),command=command)
        cls.lib=c.CDLL(str(target));cls.probe=cls.lib.fixture_probe
        cls.probe.argtypes=[c.c_uint]*5+[c.POINTER(c.c_uint16),c.POINTER(c.c_uint),c.POINTER(c.c_uint)]
        cls.probe.restype=c.c_int
    def test_ta_ack_then_sixteen_bits_and_idle(self):
        for variant in (0,1):
            for phy,reg in ((0,2),(1,3),(31,31)):
                for value in (0x001c,0xc916,0,0xffff,0xa55a):
                    for ack in (0,1):
                        out=c.c_uint16();clocks=c.c_uint();bad=c.c_uint()
                        rc=self.probe(variant,phy,reg,value,ack,c.byref(out),c.byref(clocks),c.byref(bad))
                        self.assertEqual((rc,out.value,clocks.value,bad.value),(-1 if ack else 0,value,64,0))

if __name__=='__main__':
    run=unittest.main(exit=False)
    OP.report.update(passed=run.result.wasSuccessful(),checks=run.result.testsRun)
    OP.save();print('Host test receipt:',OP.path)
    raise SystemExit(0 if run.result.wasSuccessful() else 1)
