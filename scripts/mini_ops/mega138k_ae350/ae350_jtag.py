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
from gateware.machines.mega138k_ae350.address_map import ROM_BASE

REG_NAMES = {
    'pc': 0x7b1, 'dpc': 0x7b1, 'dcsr': 0x7b0,
    'sstatus': 0x100, 'stvec': 0x105, 'sscratch': 0x140,
    'sepc': 0x141, 'scause': 0x142, 'stval': 0x143, 'satp': 0x180,
    'mstatus': 0x300, 'misa': 0x301, 'mie': 0x304, 'mtvec': 0x305,
    'mscratch': 0x340, 'mepc': 0x341, 'mcause': 0x342, 'mtval': 0x343, 'mip': 0x344,
    'milmb': 0x7c0, 'mdlmb': 0x7c1, 'micm_cfg': 0xfc0, 'mdcm_cfg': 0xfc1, 'mmsc_cfg': 0xfc2,
    'mcache_ctl': 0x7ca, 'mcctlbeginaddr': 0x7cb, 'mcctlcommand': 0x7cc, 'mcctldata': 0x7cd,
    'mmisc_ctl': 0x7d0, 'mxstatus': 0x7c4, 'pmacfg0': 0xbc0, 'pmaaddr0': 0xbd0,
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


FTDI_SIO = Path('/sys/bus/usb/drivers/ftdi_sio')


def ftdi_sio_interface(selector, driver=FTDI_SIO):
    """Kernel-bound USB interface behind a D2XX `serial:<device serial><A|B|..>` selector."""
    if os.name == 'nt' or not str(selector).startswith('serial:'):
        return None
    wanted = str(selector)[7:]
    for link in sorted(driver.glob('*:*')):
        interface = link.resolve()
        try:
            serial = (interface.parent / 'serial').read_text().strip()
            number = int((interface / 'bInterfaceNumber').read_text(), 16)
        except (OSError, ValueError):
            continue
        if serial + chr(ord('A') + number) == wanted:
            return link.name
    return None


def ftdi_sio_write(action, interface):
    try:
        (FTDI_SIO / action).write_text(interface)
    except PermissionError:
        subprocess.run(['sudo', '-n', 'tee', str(FTDI_SIO / action)],
                       input=interface.encode(), check=True, stdout=subprocess.DEVNULL)


@contextmanager
def ensure_unbound(selector):
    """D2XX cannot open an interface while ftdi_sio owns it; rebind afterwards."""
    interface = ftdi_sio_interface(selector)
    if interface:
        ftdi_sio_write('unbind', interface)
        time.sleep(0.05)
    try:
        yield
    finally:
        if interface:
            ftdi_sio_write('bind', interface)


class CPUJTAG:
    def __init__(self, location, dll):
        self.dll = (c.WinDLL if os.name == 'nt' else c.CDLL)(str(dll))
        self.handle = c.c_void_p()
        selector = str(location)
        self._driver = ensure_unbound(selector)
        self._driver.__enter__()
        try:
            if selector.startswith('serial:'):
                serial = selector[7:].encode('ascii')
                if not serial or len(serial) > 63:
                    raise ValueError('Expected serial:<FTDI interface serial>')
                self.check(self.dll.FT_OpenEx(c.c_char_p(serial), 1, c.byref(self.handle)))
            else:
                if os.name != 'nt':
                    raise ValueError('Linux D2XX requires serial:<FTDI interface serial> rather than a Windows location')
                self.check(self.dll.FT_OpenEx(c.c_void_p(int(location)), 4, c.byref(self.handle)))
        except BaseException:
            self._driver.__exit__(None, None, None)
            raise
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
            self._driver.__exit__(None, None, None)
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
            self._driver.__exit__(None, None, None)
            self.check(status)


def restart(location, dll, expected_rom, before_resume):
    """Verify the selected ROM, then restart its entry after USB programming."""
    cpu = CPUJTAG(location, dll)
    try:
        cpu.halt()
        before = {name: cpu.register(number) for name, number in
                  [('pc', 0x7b1), ('mcause', 0x342), ('mepc', 0x341), ('mtval', 0x343)]}
        if cpu.read_memory(ROM_BASE, len(expected_rom)) != expected_rom:
            raise ValueError('JTAG ROM readback differs from selected firmware')
        cpu.register(0x7b1, ROM_BASE)
        if cpu.register(0x7b1) != ROM_BASE:
            raise RuntimeError('AE350 DPC restart verification failed')
        before_resume()
        status = cpu.resume()
        return dict(idcode=f'0x{cpu.idcode:08x}', dtmcs=cpu.dtmcs,
                    rom_readback_bytes=len(expected_rom), rom_matched=True,
                    registers_before=before, restart_pc=ROM_BASE, dmstatus_resumed=status)
    finally:
        cpu.close()


# Argument kinds: 'n' integer, 'r' register name or number; '?' marks an optional trailing argument.
COMMANDS = {
    'status': (), 'halt': (), 'resume': (), 'pc': (),
    'step': ('n?',), 'restart': ('n?',), 'set-pc': ('n',), 'sleep': ('n',),
    'read-word': ('n',), 'write-word': ('n', 'n'), 'read-mem': ('n', 'n'),
    'read-reg': ('r',), 'write-reg': ('r', 'n'),
    'expect-word': ('n', 'n', 'n?'), 'expect-reg': ('r', 'n', 'n?'),
}
SCRIPT_HELP = """JTAG script: one command per line or separated by ';', '#' starts a comment.
The whole script is parsed before the cable is opened and runs in one JTAG session.
  status | pc | halt | resume       core registers / PC / explicit run control
  step [N]                          single-step N instructions (leaves the hart halted)
  restart [ADDR]                    set DPC (default 0x80000000) and resume
  set-pc ADDR
  read-word ADDR | write-word ADDR VAL | read-mem ADDR LEN
  read-reg REG | write-reg REG VAL  REG: name (pc, mepc, satp, milmb, a0, ...) or CSR/abstract number
  expect-word ADDR VAL [MASK]       fail the operation (exit 1) unless (value & MASK) == (VAL & MASK)
  expect-reg REG VAL [MASK]
  sleep MS
Register and memory commands halt a running hart only for that access; halt/step/resume/restart
change the run state explicitly and the hart is left in the state the script ends in.
Example: --exec 'halt; read-reg milmb; read-word 0xa0002080; expect-reg satp 0 0x80000000; resume'"""


def parse_script(text):
    steps = []
    for number, line in enumerate(text.splitlines(), 1):
        for part in line.split('#', 1)[0].split(';'):
            words = part.split()
            if not words:
                continue
            name, values = words[0].lower(), words[1:]
            kinds = COMMANDS.get(name)
            if kinds is None:
                raise ValueError(f'JTAG script line {number}: unknown command {name!r}')
            if not sum(not kind.endswith('?') for kind in kinds) <= len(values) <= len(kinds):
                raise ValueError(f'JTAG script line {number}: {name} takes {" ".join(kinds) or "no arguments"}')
            try:
                parsed = [resolve_reg(value) if kind[0] == 'r' else int(value, 0) for kind, value in zip(kinds, values)]
            except ValueError:
                raise ValueError(f'JTAG script line {number}: bad argument in {part.strip()!r}') from None
            if any(value < 0 or value > 0xffffffff for value in parsed):
                raise ValueError(f'JTAG script line {number}: argument outside uint32 in {part.strip()!r}')
            if name in ('read-word', 'write-word', 'expect-word', 'read-mem') and parsed[0] & 3:
                raise ValueError(f'JTAG script line {number}: address must be word aligned')
            if name == 'read-mem' and not 0 < parsed[1] <= 0x100000:
                raise ValueError(f'JTAG script line {number}: read-mem length must be 1..0x100000')
            if name == 'step' and parsed and parsed[0] == 0:
                raise ValueError(f'JTAG script line {number}: step count must be positive')
            steps.append(dict(line=number, text=' '.join(words), command=name, words=values, args=parsed))
    if not steps:
        raise ValueError('JTAG script contains no commands')
    return steps


def legacy_commands(args):
    """Translate the single-operation flags into script commands."""
    commands = []
    if getattr(args, 'halt', False): commands.append('halt')
    if getattr(args, 'resume', False): commands.append('resume')
    if getattr(args, 'step', False): commands.append('step')
    if getattr(args, 'restart', None) is not None: commands.append(f'restart {args.restart:#x}')
    elif getattr(args, 'reset', False): commands.append('restart')
    if getattr(args, 'pc', False): commands.append('pc')
    if getattr(args, 'set_pc', None) is not None: commands.append(f'set-pc {args.set_pc:#x}')
    if getattr(args, 'read_word', None) is not None: commands.append(f'read-word {args.read_word:#x}')
    if getattr(args, 'write_word', None) is not None: commands.append('write-word %#x %#x' % tuple(args.write_word))
    if getattr(args, 'read_mem', None) is not None: commands.append('read-mem %#x %#x' % tuple(args.read_mem))
    if getattr(args, 'read_reg', None) is not None: commands.append(f'read-reg {args.read_reg}')
    if getattr(args, 'write_reg', None) is not None: commands.append('write-reg %s %s' % tuple(args.write_reg))
    return commands


def script_text(args):
    parts = []
    if getattr(args, 'script', None):
        parts.append(sys.stdin.read() if args.script == '-' else Path(args.script).read_text(encoding='utf-8'))
    parts += getattr(args, 'exec', None) or []
    legacy = legacy_commands(args)
    if parts and legacy:
        raise ValueError('--script/--exec cannot be combined with single-operation flags')
    if len(legacy) > 1:
        raise ValueError('Several single-operation flags given; use --exec "cmd; cmd" to run a sequence')
    return '\n'.join(parts or legacy or ['status'])


@contextmanager
def stopped(cpu, halted):
    if not halted:
        cpu.halt()
    try:
        yield
    finally:
        if not halted:
            cpu.resume()


def hexdump(address, raw):
    return '\n'.join(f'  {address + off:08x}: {" ".join(f"{b:02x}" for b in raw[off:off + 16]):<48}  '
                     f'|{"".join(chr(b) if 32 <= b < 127 else "." for b in raw[off:off + 16])}|'
                     for off in range(0, len(raw), 16))


def execute(cpu, state, step):
    """Run one parsed command; return (printed message, receipt values)."""
    name, args, words = step['command'], step['args'], step['words']
    h = '0x{:08x}'.format
    if name == 'halt':
        if not state['halted']:
            cpu.halt()
        state['halted'] = True
        pc = cpu.register(0x7b1)
        return f'halted at pc={h(pc)}', dict(pc=h(pc))
    if name == 'resume':
        if state['halted']:
            cpu.resume()
        state['halted'] = False
        return 'resumed', {}
    if name == 'step':
        count = args[0] if args else 1
        for _ in range(count):
            pc = cpu.step()
        state['halted'] = True
        return f'stepped {count} to pc={h(pc)}', dict(count=count, pc=h(pc))
    if name == 'restart':
        entry = args[0] if args else ROM_BASE
        if not state['halted']:
            cpu.halt()
        cpu.register(0x7b1, entry)
        cpu.resume()
        state['halted'] = False
        return f'restarted at {h(entry)}', dict(entry=h(entry))
    if name == 'sleep':
        time.sleep(args[0] / 1000)
        return f'slept {args[0]} ms', dict(ms=args[0])
    with stopped(cpu, state['halted']):
        if name == 'status':
            regs = {key: h(cpu.register(number)) for key, number in
                    [('pc', 0x7b1), ('sp', 0x1002), ('ra', 0x1001), ('dcsr', 0x7b0), ('mstatus', 0x300),
                     ('mepc', 0x341), ('mcause', 0x342), ('mtval', 0x343)]}
            return '\n'.join(f'  {key:8s}: {value}' for key, value in regs.items()), dict(registers=regs)
        if name == 'pc':
            pc = cpu.register(0x7b1)
            return f'pc = {h(pc)}', dict(pc=h(pc))
        if name == 'set-pc':
            cpu.register(0x7b1, args[0])
            readback = cpu.register(0x7b1)
            return f'pc <= {h(args[0])} (readback {h(readback)})', dict(value=h(args[0]), readback=h(readback))
        if name in ('read-word', 'expect-word'):
            value = int.from_bytes(cpu.read_memory(args[0], 4), 'little')
            if name == 'read-word':
                return f'[{h(args[0])}] = {h(value)}', dict(address=h(args[0]), value=h(value))
            mask = args[2] if len(args) > 2 else 0xffffffff
            matched = value & mask == args[1] & mask
            return (f'[{h(args[0])}] = {h(value)} expect {h(args[1])} mask {h(mask)}: {"OK" if matched else "MISMATCH"}',
                    dict(address=h(args[0]), value=h(value), expected=h(args[1]), mask=h(mask), matched=matched))
        if name == 'write-word':
            cpu.write_word(args[0], args[1])
            readback = int.from_bytes(cpu.read_memory(args[0], 4), 'little')
            return (f'[{h(args[0])}] <= {h(args[1])} (readback {h(readback)})',
                    dict(address=h(args[0]), value=h(args[1]), readback=h(readback)))
        if name == 'read-mem':
            raw = cpu.read_memory(args[0], args[1])
            return f'[{h(args[0])}..+{args[1]:#x}]\n{hexdump(args[0], raw)}', dict(address=h(args[0]), length=args[1], hex=raw.hex())
        if name in ('read-reg', 'expect-reg'):
            value = cpu.register(args[0])
            label = f'{words[0]} ({args[0]:#x})'
            if name == 'read-reg':
                return f'{label} = {h(value)}', dict(reg=words[0], number=hex(args[0]), value=h(value))
            mask = args[2] if len(args) > 2 else 0xffffffff
            matched = value & mask == args[1] & mask
            return (f'{label} = {h(value)} expect {h(args[1])} mask {h(mask)}: {"OK" if matched else "MISMATCH"}',
                    dict(reg=words[0], number=hex(args[0]), value=h(value), expected=h(args[1]), mask=h(mask), matched=matched))
        if name == 'write-reg':
            cpu.register(args[0], args[1])
            readback = cpu.register(args[0])
            return (f'{words[0]} ({args[0]:#x}) <= {h(args[1])} (readback {h(readback)})',
                    dict(reg=words[0], number=hex(args[0]), value=h(args[1]), readback=h(readback)))
    raise AssertionError(name)


def run_script(cpu, op, steps):
    state = dict(halted=cpu.is_halted())
    for step in steps:
        message, values = execute(cpu, state, step)
        print(f'{step["line"]}: {step["text"]}' + ('\n' if '\n' in message else ' -> ') + message, flush=True)
        op.record(step['command'], line=step['line'], text=step['text'], **values)
        if values.get('matched') is False:
            raise RuntimeError(f'JTAG script line {step["line"]}: {message}')
    return state['halted']


def perform(args):
    steps = parse_script(script_text(args))
    op = Operation('ae350-jtag')
    print('Operation logs:', op.path, flush=True)
    op.report['script'] = [step['text'] for step in steps]
    tools = json.loads((ROOT / '.tools.local.json').read_text(encoding='utf-8'))
    library = 'ftd2xx.dll' if os.name == 'nt' else 'libftd2xx.so'
    dll_path = Path(tools['programmer']).with_name(library)
    location = getattr(args, 'cpu_jtag_location', None)
    if not location:
        location = 'serial:FactoryAIOT ProA' if os.name != 'nt' else '1'
    op.report['cpu_jtag_location'] = location
    cpu = CPUJTAG(location, dll_path)
    try:
        dmstatus = cpu.read(0x11)
        halted = bool(dmstatus & (1 << 9))
        op.report.update(idcode=f'0x{cpu.idcode:08x}', dtmcs=f'0x{cpu.dtmcs:08x}',
                         dmstatus=f'0x{dmstatus:08x}', initially_halted=halted)
        print(f'JTAG Target: IDCODE=0x{cpu.idcode:08x} DTMCS=0x{cpu.dtmcs:08x} DMSTATUS=0x{dmstatus:08x} '
              f'({"HALTED" if halted else "RUNNING"})', flush=True)
        op.report['finally_halted'] = run_script(cpu, op, steps)
        op.report['passed'] = True
        return op
    finally:
        cpu.close(resume=False)
        op.save()
