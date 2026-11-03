"""Boot AE350 Linux through the running BIOS; never programs or resets the board."""
import hashlib
import ipaddress
import json
import re
import struct
import subprocess
import time
import zlib
from types import SimpleNamespace

from scripts.mini_ops.common import Operation
from scripts.mini_ops.primer20k_vexriscv.linux import server

# Boot file names in the SD root and on the TFTP server: BIOS `boot sd` loads
# fw_payload.bin, whose U-Boot `sdboot` loads the Image and the Linux DTB.
KERNEL, DTB = 'Image', 'mega138k_ae350.dtb'
FW_JUMP, FW_PAYLOAD = 'fw_jump.bin', 'fw_payload.bin'
UBOOT_PROMPT = b'ae350# '
FAILURES = (b'Kernel panic', b'Oops:', b'TFTP FAIL', b'LINUX SELFTEST FAIL', b'ERR NET STOP', b'ERR ADDR',
            b'FETCH FAIL', b'FETCH SD WRITE FAIL', b'SD CREATE FAIL', b'SD unavailable', b'LOAD FAIL',
            b'BOOT SD FAIL', b'FLASH FAIL', b'FLASH INSTALL REJECTED',
            b'Unhandled exception', b'** Unable to read file', b"Failed to load '", b'Bad Linux RISCV Image magic',
            b'Retry count exceeded', b'TFTP error', b'### ERROR ###')
BIOS_LOAD = re.compile(r'LOAD OK (\S+) bytes=([0-9]+) crc=([0-9a-f]{8})')
UBOOT_CRC = re.compile(r'CRC32 for ([0-9a-f]{8}) \.\.\. ([0-9a-f]{8}) ==> ([0-9a-f]{8})')
USERSPACE = (b'riscv-mini Linux init pid=1', b'SELFTEST mmap/mprotect/munmap PASS',
             b'SELFTEST fork/exec/wait PASS', b'SELFTEST timer/nanosleep PASS', b'LINUX USERSPACE PASS')


class Console:
    """BIOS/Linux UART dialogue; every byte is logged and failure markers abort."""

    def __init__(self, port, log, timeout):
        self.port, self.log, self.timeout = port, log, timeout

    def send(self, command):
        self.port.write((command+'\r').encode())
        self.port.flush()

    def until(self, marker, echo=b'', timeout=None):
        """Return output up to marker; with echo, only a marker after the command's echo counts."""
        data = bytearray()
        deadline = time.monotonic()+(timeout or self.timeout)
        while time.monotonic() < deadline:
            chunk = self.port.read(self.port.in_waiting or 1)
            data.extend(chunk)
            self.log.write(chunk)
            self.log.flush()
            for failure in FAILURES:
                if failure in data:
                    raise RuntimeError('Boot failed: '+failure.decode())
            start = data.find(echo)
            if start >= 0 and marker in data[start+len(echo):]:
                return bytes(data[start:])
        raise TimeoutError('Waiting for '+repr(marker)+'; see '+str(self.log.name))

    def command(self, text, marker=b'\n> ', timeout=None):
        self.send(text)
        return self.until(marker, text.encode(), timeout)

    def prompt(self):
        """Output left over from an earlier session (banner prompts, a late
        TFTP timeout) must not be taken as replies to these commands."""
        time.sleep(.5)
        self.port.reset_input_buffer()
        self.command('')

    def network(self, board_ip, host_ip):
        if b'BOARD IP SET OK' not in self.command('set ip '+board_ip):
            raise RuntimeError('BIOS rejected board address '+board_ip)
        self.command('set server '+host_ip)

    def uboot(self, text, marker=UBOOT_PROMPT):
        return self.command(text, marker)

    def userspace(self, op, boot, layout, uboot=False):
        """Judge an OpenSBI(+U-Boot)+Linux boot transcript ending at the shell prompt."""
        op.record('opensbi_layout', **check_banner(boot.decode(errors='replace'), layout, uboot))
        if any(marker not in boot for marker in USERSPACE):
            raise RuntimeError('Missing Linux userspace selftest evidence')
        replies = {}
        for command in ('uname', 'cpu', 'mem', 'uptime', 'test'):
            self.send(command)
            replies[command] = self.until(b'linux-mini> ').decode(errors='replace')
        if 'LINUX USERSPACE PASS' not in replies['test']:
            raise RuntimeError('Repeated userspace selftest did not pass')
        op.record('linux_shell', passed=True, output=boot.decode(errors='replace'), commands=replies)


def opensbi_image(path, layout):
    """fw_jump fits os.sbi; a FW_PAYLOAD image carries U-Boot at os.uboot, beyond it."""
    if path.stat().st_size > layout.OS_SBI_BYTES:
        return FW_PAYLOAD, layout.OS_SBI_BYTES + layout.OS_UBOOT_BYTES
    return FW_JUMP, layout.OS_SBI_BYTES


def loaded(text, layout):
    """Files a boot transcript loaded: BIOS `LOAD OK name bytes=N crc=X`, and the
    U-Boot `crc32` that follows each load at a contract address."""
    names = {layout.OS_KERNEL_BASE: KERNEL, layout.OS_DTB_BASE: DTB}
    found = {name: dict(bytes=int(size), crc32=crc) for name, size, crc in BIOS_LOAD.findall(text)}
    for start, end, crc in UBOOT_CRC.findall(text):
        if int(start, 16) in names:
            found[names[int(start, 16)]] = dict(bytes=int(end, 16)-int(start, 16)+1, crc32=crc)
    return found


def check_loaded(text, layout, files):
    """Every named host input must appear in the transcript with its length and CRC32."""
    found = loaded(text.decode(errors='replace'), layout)
    expected = {name: dict(bytes=len(data), crc32='%08x' % zlib.crc32(data)) for name, data in files}
    for name, value in expected.items():
        if found.get(name) != value:
            raise RuntimeError('Loaded boot file differs from the host input: '+name)
    return expected


def check_banner(text, layout, uboot=False):
    """The running OpenSBI must report the addresses of the address contract."""
    fields = {}
    for line in text.splitlines():
        key, _, value = line.partition(':')
        fields[key.strip()] = value.strip()
    try:
        base = int(fields['Firmware Base'], 0)
        size = int(fields['Firmware Size'].split()[0]) * 1024
        found = dict(firmware_base=base, firmware_bytes=size,
                     next_address=int(fields['Domain0 Next Address'], 0),
                     next_arg1=int(fields['Domain0 Next Arg1'], 0))
    except (KeyError, ValueError, IndexError) as error:
        raise RuntimeError('OpenSBI banner lacks layout fields') from error
    if base != layout.OS_SBI_BASE or base + size > layout.OS_SBI_BASE + layout.OS_SBI_BYTES:
        raise RuntimeError('OpenSBI runtime region differs from os.sbi')
    if uboot:
        if found['next_address'] != layout.OS_UBOOT_BASE or found['next_arg1'] != layout.OS_FIRMWARE_FDT_BASE:
            raise RuntimeError('OpenSBI next stage differs from os.uboot/os.firmware-fdt')
    elif found['next_address'] != layout.OS_KERNEL_BASE or found['next_arg1'] != layout.OS_DTB_BASE:
        raise RuntimeError('OpenSBI next stage differs from os.kernel/os.dtb')
    return found


def board_address(host_ip, board_ip=None):
    """The board must sit in the host NIC's subnet: the BIOS link-local default is only
    reachable through dhcpcd's IPv4LL route, which is dropped on every carrier loss
    (each FPGA reprogram) and restored about 10 s after link-up."""
    listing = json.loads(subprocess.run(['ip', '-j', '-4', 'addr', 'show'], check=True,
                                        capture_output=True, text=True).stdout)
    networks = [ipaddress.ip_interface('%s/%d' % (a['local'], a['prefixlen'])).network
                for link in listing for a in link.get('addr_info', []) if a.get('local') == host_ip]
    if not networks:
        raise ValueError('--host-ip %s is not configured on any local interface' % host_ip)
    network, host = networks[0], ipaddress.ip_address(host_ip)
    board = ipaddress.ip_address(board_ip) if board_ip else network.network_address+20
    if not board_ip and board == host:
        board += 1
    if board not in network or board in (host, network.network_address, network.broadcast_address):
        raise ValueError('Board address %s is not a free host address in %s' % (board, network))
    return str(board)


def perform(args):
    op = Operation('ae350-linux')
    try:
        if not args.dtb or args.initrd:
            raise ValueError('AE350 requires --dtb and embedded initramfs; --initrd is unsupported')
        if not args.port or not args.build or args.timeout <= 0:
            raise ValueError('Explicit --port, --build and positive --timeout required')
        from scripts.mini_ops.mega138k_ae350.ae350 import candidate
        _, ref, boot_id = candidate(args.build)
        op.report['selected_build'] = dict(artifact=ref, boot_id=boot_id)
        from gateware.description import resolve_machine
        from gateware.machines.mega138k_ae350.address_map import AddressMap
        layout = AddressMap(resolve_machine('mega138k_ae350'))
        layout.require()
        op.report['software_layout'] = layout.software_layout()
        board_ip = board_address(args.host_ip, getattr(args, 'board_ip', None))
        op.report['network'] = dict(host_ip=args.host_ip, board_ip=board_ip)
        firmware, firmware_bytes = opensbi_image(args.os_image, layout)
        uboot = firmware == FW_PAYLOAD
        sd_install = getattr(args, 'sd_install', False)
        if sd_install and not uboot:
            raise ValueError('BIOS `boot sd` starts the OpenSBI+U-Boot fw_payload.bin; --sd-install needs that image')
        uboot_load = (getattr(args, 'uboot_load', None) or 'sd') if uboot else None
        uboot_commands = getattr(args, 'uboot_command', None) or []
        if uboot_commands and not uboot:
            raise ValueError('--uboot-command needs the OpenSBI+U-Boot fw_payload.bin')
        op.report['boot_chain'] = dict(opensbi=firmware, uboot_load=uboot_load)
        kernel = (args.kernel, KERNEL, layout.OS_KERNEL_BASE, layout.OS_KERNEL_BYTES)
        sbi = (args.os_image, firmware, layout.OS_SBI_BASE, firmware_bytes)
        dtb_file = (args.dtb, DTB, layout.OS_DTB_BASE, layout.OS_DTB_BYTES)
        files = [kernel, sbi, dtb_file]
        # U-Boot loads the Image and DTB itself; the BIOS only starts the firmware.
        bios_files = [sbi] if uboot else files
        uboot_files = [kernel, dtb_file] if uboot else []
        image = args.kernel.read_bytes()
        if len(image) < 64 or image[48:56] != b'RISCV\0\0\0':
            raise ValueError('Invalid RISC-V Linux Image')
        footprint = struct.unpack_from('<Q', image, 16)[0]
        if footprint < len(image) or footprint > layout.OS_KERNEL_BYTES:
            raise ValueError('Kernel footprint exceeds the os.kernel range')
        dtb = args.dtb.read_bytes()
        if len(dtb) < 40 or dtb[:4] != b'\xd0\x0d\xfe\xed' or struct.unpack_from('>I', dtb, 4)[0] != len(dtb):
            raise ValueError('Invalid DTB')
        for path, name, address, size in files:
            data = path.read_bytes()
            if not data or len(data) > size:
                raise ValueError('Empty input or larger than its contract range: '+name)
            op.record('input', file=str(path.resolve()), address=hex(address), size=len(data),
                      sha256=hashlib.sha256(data).hexdigest())
        if args.dry_run:
            op.record('dry_run', hardware_access=False)
            return
        import serial
        with serial.Serial(args.port, 115200, timeout=.1) as port, (op.path/'linux-uart.log').open('wb') as log:
            console = Console(port, log, args.timeout)
            console.prompt()
            console.network(board_ip, args.host_ip)
            board = SimpleNamespace(op=op)
            if sd_install:
                # Replaces only the three boot files in the SD root; earlier sizes are kept as evidence.
                for path, name, _, _ in files:
                    old = console.command('size '+name).decode(errors='replace')
                    replaced = 'SIZE FAIL' not in old
                    if replaced and b'FILE OK' not in console.command('rm '+name):
                        raise RuntimeError('Cannot replace SD file '+name)
                    with server(board, args, path, name):
                        if ('FETCH SAVED '+name).encode() not in console.command('fetch '+name):
                            raise RuntimeError('SD install failed: '+name)
                    op.record('sd_install', file=name, replaced=replaced, previous=old.splitlines()[1:2])
                start = 'boot sd'
            else:
                for path, name, address, _ in bios_files:
                    with server(board, args, path, name):
                        reply = console.command('tftp %08x %s' % (address, name))
                        if b'TFTP OK' not in reply or ('Bytes transferred = %d' % path.stat().st_size).encode() not in reply:
                            raise RuntimeError('TFTP length mismatch: '+name)
                    if ('==> %08x' % zlib.crc32(path.read_bytes())).encode() not in console.command('crc32 %08x %x' % (address, path.stat().st_size)).lower():
                        raise RuntimeError('CRC mismatch: '+name)
                    op.record('transfer', file=name, crc_verified=True)
                start = 'boot opensbi'
            if uboot_load == 'net' or uboot_commands:
                # Stop the SD autoboot; U-Boot fetches each file from a single-file TFTP server.
                boot = console.command(start, b'Hit any key to stop autoboot')
                console.send('')
                boot += console.until(UBOOT_PROMPT)
                for text in uboot_commands:
                    reply = console.uboot(text)
                    op.record('uboot_command', command=text, output=reply.decode(errors='replace'))
                    boot += reply
                if uboot_load == 'sd':
                    boot += console.uboot('run sdboot', b'linux-mini> ')
                else:
                    boot += console.uboot('setenv ipaddr %s; setenv serverip %s' % (board_ip, args.host_ip))
                    for path, name, address, _ in uboot_files:
                        with server(board, args, path, name):
                            reply = console.uboot('tftpboot %08x %s' % (address, name))
                        if ('Bytes transferred = %d ' % path.stat().st_size).encode() not in reply:
                            raise RuntimeError('U-Boot TFTP length mismatch: '+name)
                        boot += reply+console.uboot('crc32 %08x ${filesize}' % address)
                    boot += console.uboot('booti ${kernel_addr_r} - ${fdt_addr_r}', b'linux-mini> ')
            else:
                boot = console.command(start, b'linux-mini> ')
            loads = check_loaded(boot, layout, [(name, path.read_bytes()) for path, name, _, _ in
                                                (files if sd_install else uboot_files)])
            if sd_install:
                op.report['sd_files'] = loads
            elif loads:
                op.record('uboot_load', source=uboot_load, files=loads)
            console.userspace(op, boot, layout, uboot)
            if args.devices:
                # Enabled device nodes only; the USB disk tail is saved, rewritten and restored.
                # The kernel log keeps the driver probe and USB topology as evidence.
                devices = {}
                for command in ('usb', 'dmesg'):
                    console.send(command)
                    devices[command] = console.until(b'linux-mini> ').decode(errors='replace')
                if 'SELFTEST USB-storage read/write/restore PASS' not in devices['usb']:
                    raise RuntimeError('USB storage selftest did not pass')
                op.record('linux_devices', passed=True, commands=devices)

            op.report['passed'] = True
    except Exception as error:
        op.report['error'] = str(error)
        raise
    finally:
        op.save()
        print('Operation logs:', op.path)
