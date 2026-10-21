"""UART boot session and programmer result classification.

Machine-neutral: the 20K loader and the AE350 ROM/POST flows both speak the
same ``STATE=``/``BL>`` protocol and the same image framing.  Platform-specific
programming and Flash layout stay in the platform scripts.
"""
import re
import struct
import time

from tools.boot_image import CHUNK, HEADER, packet, unpack_image


def programmer_succeeded(result):
    # Gowin can emit "Error: SPI Verify failed!" and still return 0/Finished.
    output=result.stdout+'\n'+result.stderr
    return result.returncode==0 and bool(re.search(r'\bFinished[.!]',output)) and not re.search(r'\berror\s*:|\bfailed\b',output,re.I)


class BootSession:
    def __init__(self,port,log=None):
        self.port=port
        self.log=log
    def note(self,data):
        if self.log:
            self.log.write(data);self.log.flush()
    def until(self,marker,timeout=120,on_chunk=None):
        """Wait for `marker`.  `on_chunk(data)` is called for every chunk as it
        arrives so a caller can stream a slow board instead of going silent
        until the marker lands (or the wait times out)."""
        data=bytearray();deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            # Small reads keep ACK/command boundaries intact.
            chunk=self.port.read(1)
            if chunk:
                if on_chunk:on_chunk(chunk)
                data.extend(chunk)
                if data.endswith(b'ERR DDR INIT/TIMEOUT; reset\r\n') or data.endswith(b'ERR DDR INIT; reset\r\n'):
                    self.note(data)
                    raise RuntimeError('DDR initialization failed; reset required')
                if data.endswith(marker):
                    self.note(data)
                    return bytes(data)
        self.note(data)
        raise TimeoutError(f'Waiting for {marker!r}; received {bytes(data[-1000:])!r}')
    def wait_state(self,expected,timeout=120,abi=None):
        """Canonical state wait: fails immediately on a FAILED classification."""
        from tools.uart_state import wait_for
        data,detail=wait_for(self.port,expected,timeout,abi,self.log)
        return data,detail
    def fresh_banner(self,timeout=120,pattern=None):
        # A previous binary readback may still be draining from the UART FIFO.
        # Log it, but do not interpret embedded firmware strings as status.
        pattern=pattern or rb'(?:^|\n)(riscv-mini (?:ROM|XIP) [\x20-\x7e]+\r*\nDDR INIT [0-9]+\r*\n)$'
        data=bytearray();lines=bytearray();deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            chunk=self.port.read(1)
            if not chunk:continue
            data.extend(chunk);lines.extend(chunk)
            # Try on every byte: a loader prompt such as "BL> " has no trailing
            # newline, so matching only at line ends would never fire.
            match=re.search(pattern,lines)
            if match:
                self.note(data)
                return match.group(1)
            lines=lines[-512:]
        self.note(data)
        raise TimeoutError('Waiting for fresh loader banner; received '+repr(bytes(data[-1000:])))
    def menu(self,reset=False,fresh=False):
        if reset:
            self.port.reset_input_buffer();self.port.write(b'!');self.port.flush()
        banner=self.fresh_banner() if fresh else b''
        data=banner+self.until(b'BOOT SELECT:')
        self.port.write(b'b');self.port.flush()
        return data+self.until(b'BL> ')
    def command(self,value,timeout=120):
        self.port.write(value.encode());self.port.flush()
        return self.until(b'BL> ',timeout)
    def upload(self,image,install=False,load=None,abi=None):
        _,payload=unpack_image(image,abi=abi,load=load)
        self.port.write(b'p' if install else b'u');self.port.flush()
        self.until(b'READY HEADER\r\n')
        self.port.write(image[:HEADER.size]);self.port.flush()
        self.until(b'READY DATA\r\n')
        for sequence,start in enumerate(range(0,len(payload),CHUNK)):
            self.port.write(packet(sequence,payload[start:start+CHUNK]));self.port.flush()
            ack=bytearray();deadline=time.monotonic()+5
            while len(ack)<5 and time.monotonic()<deadline: ack.extend(self.port.read(5-len(ack)))
            if bytes(ack)!=b'K'+struct.pack('<I',sequence):
                self.note(ack)
                raise RuntimeError(f'Bad ACK at packet {sequence}: {bytes(ack)!r}')
        result=self.until(b'BL> ' if install else b'> ')
        if b'ERR ' in result or (install and b'FLASH INSTALLED' not in result):
            raise RuntimeError(result.decode(errors='replace'))
        if not install and b'SYSTEM READY' not in result:
            raise RuntimeError('Application did not become ready: '+result.decode(errors='replace'))
        return result
