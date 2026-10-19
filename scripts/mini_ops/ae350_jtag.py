"""AE350 D2XX/JTAG operations using the installed Windows FTDI driver."""
import ctypes as c
import time


class CPUJTAG:
    def __init__(self, location, dll):
        self.dll = c.WinDLL(str(dll))
        self.handle = c.c_void_p()
        self.check(self.dll.FT_OpenEx(c.c_void_p(int(location)), 4, c.byref(self.handle)))
        self.closed = False
        self.active = False
        try:
            self.call('FT_SetTimeouts', 2000, 2000)
            self.call('FT_SetLatencyTimer', 2)
            self.call('FT_Purge', 3)
            self.call('FT_SetBitMode', 0, 0)
            self.call('FT_SetBitMode', 0x0b, 2)
            time.sleep(.05)
            self.send(bytes([0x80, 8, 0x0b, 0x86, 59, 0, 0x85]))
            self.tms([1] * 6 + [0])
            self.idcode = self.scan(32)
            if self.idcode != 0x1000563d:
                raise ValueError(f'Expected AE350 CPU TAP, got 0x{self.idcode:08x}')
            self.scan(5, 0x10, ir=True)
            self.dtmcs = self.scan(32)
            self.bits = 34 + ((self.dtmcs >> 4) & 63)
            if self.dtmcs & 15 != 1 or self.bits != 41:
                raise ValueError(f'Unsupported AE350 DTMCS: 0x{self.dtmcs:x}')
            self.scan(32, 1 << 16)
            self.scan(5, 0x11, ir=True)
            self.write(0x10, 1)
            self.active = True
        except Exception:
            self.dll.FT_Close(self.handle)
            raise

    @staticmethod
    def check(status):
        if status:
            raise RuntimeError('FTDI D2XX status ' + str(status))

    def call(self, name, *args):
        self.check(getattr(self.dll, name)(self.handle, *args))

    def send(self, data):
        count = c.c_ulong()
        self.call('FT_Write', c.create_string_buffer(data), len(data), c.byref(count))
        if count.value != len(data):
            raise RuntimeError('Short D2XX JTAG write')

    def tms(self, bits):
        self.send(bytes(sum(([0x4b, 0, bit] for bit in bits), [])))

    def scan(self, bits, value=0, ir=False):
        self.tms([1, 1, 0, 0] if ir else [1, 0, 0])
        data = bytes(sum(([0x6b, 0, int(n == bits - 1) | (((value >> n) & 1) << 7)]
                          for n in range(bits)), []))
        self.send(data + b'\x87')
        buffer, got = c.create_string_buffer(bits), c.c_ulong()
        self.call('FT_Read', buffer, bits, c.byref(got))
        if got.value != bits:
            raise RuntimeError(f'Short D2XX JTAG read: {got.value}/{bits}')
        self.tms([1, 0])
        return sum(((byte >> 7) & 1) << n for n, byte in enumerate(buffer.raw))

    def transaction(self, address, value=0, operation=1):
        self.scan(self.bits, (address << 34) | (value << 2) | operation)
        self.tms([0] * 12)
        result = self.scan(self.bits)
        self.tms([0] * 12)
        if result & 3:
            raise RuntimeError(f'AE350 DMI error: address={address:x}, result={result:x}')
        return (result >> 2) & 0xffffffff

    def read(self, address):
        return self.transaction(address)

    def write(self, address, value):
        self.transaction(address, value, 2)

    def command(self, value):
        self.write(0x16, 7 << 8)
        self.write(0x17, value)
        for _ in range(100):
            status = self.read(0x16)
            if not status & (1 << 12):
                if status & (7 << 8):
                    raise RuntimeError(f'AE350 abstract command error: {status:08x}')
                return
        raise RuntimeError('AE350 abstract command busy timeout')

    def register(self, number, value=None):
        if value is not None:
            self.write(4, value)
        self.command((2 << 20) | (1 << 17) | ((value is not None) << 16) | number)
        return self.read(4)

    def wait_status(self, mask):
        for _ in range(100):
            status = self.read(0x11)
            if status & mask == mask:
                return status
            time.sleep(.005)
        raise RuntimeError(f'AE350 DMSTATUS timeout: {status:08x}')

    def halt(self):
        # Keep haltreq asserted while the core is halted.  The measured DM
        # sequence shows that clearing haltreq alone does NOT resume the hart
        # (dmstatus stays allhalted=1), so this is a bookkeeping choice, not a
        # workaround for a hardware quirk.  Abstract memory access returns
        # cmderr=4 (08000404) whenever the hart is *running*, so callers must
        # halt again before any memory access after a resume.
        self.write(0x10, 0x80000001)
        return self.wait_status(1 << 9)

    def resume(self):
        self.write(0x10, 0x40000001)
        status = self.wait_status(1 << 11)
        self.write(0x10, 1)
        return status

    def read_memory(self, address, length):
        data = bytearray()
        for offset in range(0, length, 4):
            self.write(5, address + offset)
            self.command(0x02200000)
            data.extend(self.read(4).to_bytes(4, 'little'))
        return bytes(data[:length])

    def write_word(self, address, value):
        if address & 3 or not 0 <= value <= 0xffffffff:
            raise ValueError('Abstract word write requires an aligned address and uint32 value')
        self.write(4, value)
        self.write(5, address)
        self.command(0x02210000)

    def close(self, resume=True):
        if self.closed:
            return
        try:
            if self.active and resume:
                self.resume()
        finally:
            status = self.dll.FT_Close(self.handle)
            self.closed = True
            self.active = False
            self.check(status)


def restart(location, dll, expected_rom, before_resume):
    """Verify the selected ROM, then restart its entry after USB programming."""
    cpu = CPUJTAG(location, dll)
    try:
        cpu.halt()
        before = {name: cpu.register(number) for name, number in
                  [('pc', 0x7b1), ('mcause', 0x342), ('mepc', 0x341), ('mtval', 0x343)]}
        if cpu.read_memory(0x80000000, len(expected_rom)) != expected_rom:
            raise ValueError('JTAG ROM readback differs from selected firmware')
        cpu.register(0x7b1, 0x80000000)
        if cpu.register(0x7b1) != 0x80000000:
            raise RuntimeError('AE350 DPC restart verification failed')
        before_resume()
        status = cpu.resume()
        return dict(idcode=f'0x{cpu.idcode:08x}', dtmcs=cpu.dtmcs,
                    rom_readback_bytes=len(expected_rom), rom_matched=True,
                    registers_before=before, restart_pc=0x80000000, dmstatus_resumed=status)
    finally:
        cpu.close()
