"""AE350 D2XX/JTAG using the installed Windows or Linux FTDI library."""
import ctypes as c
import json
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import sys
sys.path[:0] = [str(Path(__file__).resolve().parents[3]), str(Path(__file__).resolve().parents[3] / 'scripts')]
from scripts.tools.build_records import ROOT
from mini_ops.common import Operation

REG_NAMES = {
    'pc': 0x7b1, 'dpc': 0x7b1, 'dcsr': 0x7b0,
    'mstatus': 0x300, 'misa': 0x301, 'mie': 0x304, 'mtvec': 0x305,
    'mscratch': 0x340, 'mepc': 0x341, 'mcause': 0x342, 'mtval': 0x343, 'mip': 0x344,
    'milmb': 0x7c0, 'mdlmb': 0x7c1, 'micm_cfg': 0xfc0, 'mdcm_cfg': 0xfc1,
    'zero': 0x1000, 'ra': 0x1001, 'sp': 0x1002, 'gp': 0x1003, 'tp': 0x1004,
    't0': 0x1005, 't1': 0x1006, 't2': 0x1007, 's0': 0x1008, 'fp': 0x1008, 's1': 0x1009,
    'a0': 0x100a, 'a1': 0x100b, 'a2': 0x100c, 'a3': 0x100d, 'a4': 0x100e, 'a5': 0x100f,
    'a6': 0x1010, 'a7': 0x1011, 's2': 0x1012, 's3': 0x1013, 's4': 0x1014, 's5': 0x1015,
    's6': 0x1016, 's7': 0x1017, 's8': 0x1018, 's9': 0x1019, 's10': 0x101a, 's11': 0x101b,
    't3': 0x101c, 't4': 0x101d, 't5': 0x101e, 't6': 0x101f
}
for i in range(32):
    REG_NAMES[f'x{i}'] = 0x1000 + i


def resolve_reg(name):
    s = str(name).strip().lower()
    if s in REG_NAMES:
        return REG_NAMES[s]
    return int(s, 0)


@contextmanager
def ensure_unbound(device='1-4:1.0'):
    unbound = False
    if os.name != 'nt' and Path(f'/sys/bus/usb/drivers/ftdi_sio/{device}').exists():
        try:
            with open('/sys/bus/usb/drivers/ftdi_sio/unbind', 'w') as f:
                f.write(device)
            unbound = True
        except PermissionError:
            subprocess.run(['sudo', '-n', 'tee', '/sys/bus/usb/drivers/ftdi_sio/unbind'],
                           input=device.encode(), check=True, stdout=subprocess.DEVNULL)
            unbound = True
        time.sleep(0.05)
    try:
        yield
    finally:
        if unbound:
            try:
                with open('/sys/bus/usb/drivers/ftdi_sio/bind', 'w') as f:
                    f.write(device)
            except Exception:
                subprocess.run(['sudo', '-n', 'tee', '/sys/bus/usb/drivers/ftdi_sio/bind'],
                               input=device.encode(), stdout=subprocess.DEVNULL)


class CPUJTAG:
    def __init__(self, location, dll):
        self.dll = (c.WinDLL if os.name == 'nt' else c.CDLL)(str(dll))
        self.handle = c.c_void_p()
        selector = str(location)
        if selector.startswith('serial:'):
            serial = selector[7:].encode('ascii')
            if not serial or len(serial) > 63:
                raise ValueError('Expected serial:<FTDI interface serial>')
            self.check(self.dll.FT_OpenEx(c.c_char_p(serial), 1, c.byref(self.handle)))
        else:
            if os.name != 'nt':
                raise ValueError('Linux D2XX requires serial:<FTDI interface serial> rather than a Windows location')
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
        count = c.c_uint32()
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
        buffer, got = c.create_string_buffer(bits), c.c_uint32()
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

    def is_halted(self):
        return bool(self.read(0x11) & (1 << 9))

    def halt(self):
        # Keep haltreq asserted while the core is halted.
        self.write(0x10, 0x80000001)
        return self.wait_status(1 << 9)

    def resume(self):
        self.write(0x10, 0x40000001)
        status = self.wait_status(1 << 11)
        self.write(0x10, 1)
        return status

    def step(self):
        was_running = not self.is_halted()
        if was_running:
            self.halt()
        dcsr = self.register(0x7b0)
        self.register(0x7b0, dcsr | 4)
        self.write(0x10, 0x40000001)
        self.wait_status(1 << 9)
        self.register(0x7b0, dcsr & ~4)
        return self.register(0x7b1)

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


def perform(args):
    op = Operation('ae350-jtag')
    print('Operation logs:', op.path, flush=True)
    tools = json.loads((ROOT / '.tools.local.json').read_text(encoding='utf-8'))
    library = 'ftd2xx.dll' if os.name == 'nt' else 'libftd2xx.so'
    dll_path = Path(tools['programmer']).with_name(library)
    location = getattr(args, 'cpu_jtag_location', None)
    if not location:
        location = 'serial:FactoryAIOT ProA' if os.name != 'nt' else '1'

    op.report['cpu_jtag_location'] = location
    with ensure_unbound():
        cpu = CPUJTAG(location, dll_path)
        try:
            initially_halted = cpu.is_halted()
            dmstatus = cpu.read(0x11)
            op.report['idcode'] = f'0x{cpu.idcode:08x}'
            op.report['dtmcs'] = f'0x{cpu.dtmcs:08x}'
            op.report['dmstatus'] = f'0x{dmstatus:08x}'
            op.report['initially_halted'] = initially_halted

            # Determine action
            action = 'status'
            if getattr(args, 'halt', False):
                action = 'halt'
            elif getattr(args, 'resume', False):
                action = 'resume'
            elif getattr(args, 'step', False):
                action = 'step'
            elif getattr(args, 'reset', False) or getattr(args, 'restart', None) is not None:
                action = 'reset'
            elif getattr(args, 'pc', False):
                action = 'pc'
            elif getattr(args, 'set_pc', None) is not None:
                action = 'set-pc'
            elif getattr(args, 'read_word', None) is not None:
                action = 'read-word'
            elif getattr(args, 'write_word', None) is not None:
                action = 'write-word'
            elif getattr(args, 'read_mem', None) is not None:
                action = 'read-mem'
            elif getattr(args, 'read_reg', None) is not None:
                action = 'read-reg'
            elif getattr(args, 'write_reg', None) is not None:
                action = 'write-reg'
            elif hasattr(args, 'action') and args.action != 'status':
                action = args.action

            op.report['action'] = action
            print(f'JTAG Target: IDCODE=0x{cpu.idcode:08x} DTMCS=0x{cpu.dtmcs:08x} DMSTATUS=0x{dmstatus:08x} ({"HALTED" if initially_halted else "RUNNING"})')

            if action == 'halt':
                if not initially_halted:
                    cpu.halt()
                pc = cpu.register(0x7b1)
                print(f'Halted CPU at PC: 0x{pc:08x}')
                op.report['pc'] = f'0x{pc:08x}'
                op.report['halted'] = True
                op.report['passed'] = True
                return op

            if action == 'resume':
                if initially_halted:
                    cpu.resume()
                print('Resumed CPU execution')
                op.report['halted'] = False
                op.report['passed'] = True
                return op

            if action == 'step':
                new_pc = cpu.step()
                print(f'Stepped CPU to PC: 0x{new_pc:08x}')
                op.report['pc'] = f'0x{new_pc:08x}'
                op.report['halted'] = True
                op.report['passed'] = True
                return op

            if action == 'reset':
                target_addr = getattr(args, 'restart', None) or 0x80000000
                cpu.halt()
                cpu.register(0x7b1, target_addr)
                cpu.resume()
                print(f'Restarted CPU at entry 0x{target_addr:08x} and resumed execution')
                op.report['restart_entry'] = f'0x{target_addr:08x}'
                op.report['passed'] = True
                return op

            # For inspecting/modifying registers or memory: halt temporarily if running
            if not initially_halted:
                cpu.halt()

            try:
                if action == 'pc':
                    pc = cpu.register(0x7b1)
                    print(f'Current PC: 0x{pc:08x}')
                    op.report['pc'] = f'0x{pc:08x}'

                elif action == 'set-pc':
                    target_pc = args.set_pc
                    cpu.register(0x7b1, target_pc)
                    verified_pc = cpu.register(0x7b1)
                    print(f'Set PC to 0x{verified_pc:08x}')
                    op.report['pc'] = f'0x{verified_pc:08x}'

                elif action == 'read-word':
                    addr = args.read_word
                    val = int.from_bytes(cpu.read_memory(addr, 4), 'little')
                    print(f'Memory [0x{addr:08x}] = 0x{val:08x}')
                    op.report['read_word'] = dict(address=f'0x{addr:08x}', value=f'0x{val:08x}')

                elif action == 'write-word':
                    addr, val = args.write_word
                    cpu.write_word(addr, val)
                    readback = int.from_bytes(cpu.read_memory(addr, 4), 'little')
                    print(f'Memory [0x{addr:08x}] <= 0x{val:08x} (readback: 0x{readback:08x})')
                    op.report['write_word'] = dict(address=f'0x{addr:08x}', value=f'0x{val:08x}', readback=f'0x{readback:08x}')

                elif action == 'read-mem':
                    addr, length = args.read_mem
                    raw = cpu.read_memory(addr, length)
                    print(f'Memory [0x{addr:08x}..+0x{length:x}]:')
                    for off in range(0, len(raw), 16):
                        chunk = raw[off:off+16]
                        hex_str = ' '.join(f'{b:02x}' for b in chunk)
                        ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
                        print(f'  {addr+off:08x}: {hex_str:<48}  |{ascii_str}|')
                    op.report['read_mem'] = dict(address=f'0x{addr:08x}', length=length, hex=raw.hex())

                elif action == 'read-reg':
                    reg_num = resolve_reg(args.read_reg)
                    val = cpu.register(reg_num)
                    print(f'Register {args.read_reg} (0x{reg_num:x}) = 0x{val:08x}')
                    op.report['read_reg'] = dict(reg=args.read_reg, number=f'0x{reg_num:x}', value=f'0x{val:08x}')

                elif action == 'write-reg':
                    reg_name, val_str = args.write_reg
                    reg_num = resolve_reg(reg_name)
                    val = int(val_str, 0)
                    cpu.register(reg_num, val)
                    readback = cpu.register(reg_num)
                    print(f'Register {reg_name} (0x{reg_num:x}) <= 0x{val:08x} (readback: 0x{readback:08x})')
                    op.report['write_reg'] = dict(reg=reg_name, number=f'0x{reg_num:x}', value=f'0x{val:08x}', readback=f'0x{readback:08x}')

                elif action == 'status':
                    pc = cpu.register(0x7b1)
                    sp = cpu.register(0x1002)
                    ra = cpu.register(0x1001)
                    dcsr = cpu.register(0x7b0)
                    mstatus = cpu.register(0x300)
                    mepc = cpu.register(0x341)
                    mcause = cpu.register(0x342)
                    mtval = cpu.register(0x343)
                    regs = {
                        'PC': f'0x{pc:08x}',
                        'SP': f'0x{sp:08x}',
                        'RA': f'0x{ra:08x}',
                        'DCSR': f'0x{dcsr:08x}',
                        'MSTATUS': f'0x{mstatus:08x}',
                        'MEPC': f'0x{mepc:08x}',
                        'MCAUSE': f'0x{mcause:08x}',
                        'MTVAL': f'0x{mtval:08x}'
                    }
                    print('CPU Core Registers:')
                    for k, v in regs.items():
                        print(f'  {k:8s}: {v}')
                    op.report['registers'] = regs
            finally:
                if not initially_halted:
                    cpu.resume()

            op.report['passed'] = True
            return op
        finally:
            cpu.close(resume=not initially_halted and action != 'halt' and action != 'step')
            op.save()
