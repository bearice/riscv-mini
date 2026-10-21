"""Linux board acceptance using the common SRAM/UART/TFTP workflows."""
import hashlib
import struct
import subprocess
import time
from contextlib import contextmanager

from bios_image import OS_MAGIC, unpack
from tools.boot_session import BootSession
from build_records import ROOT

LOAD = 0x02000000
INITRD_LOAD = 0x04000000


def preflight(args):
    header, _ = unpack(args.os_image.read_bytes())
    if header[0] != OS_MAGIC:
        raise ValueError('Linux bootstrap requires an OSB1 OpenSBI/U-Boot image')
    image = args.kernel.read_bytes()
    if len(image) < 64 or image[48:56] != b'RISCV\x00\x00\x00' or image[56:60] != b'RSC\x05':
        raise ValueError('Not a RISC-V Linux Image')
    offset, size = struct.unpack_from('<QQ', image, 8)
    # RV32 PMD alignment; the Image includes BSS in its declared footprint.
    if offset != 0x400000 or size < len(image) or LOAD + size > 0x07000000:
        raise ValueError('Unexpected RV32 Image offset or memory footprint')
    if not args.timeout > 0:
        raise ValueError('Linux timeout must be positive')
    result = dict(load_address=hex(LOAD), text_offset=hex(offset), image_size=size,
                file_size=len(image), kernel_sha256=hashlib.sha256(image).hexdigest(),
                os_image_sha256=hashlib.sha256(args.os_image.read_bytes()).hexdigest())
    if getattr(args, 'initrd', None):
        import gzip
        raw = args.initrd.read_bytes()
        if LOAD + size > INITRD_LOAD or not raw or INITRD_LOAD + len(raw) > 0x06000000:
            raise ValueError('Initrd overlaps kernel or reserved RAM')
        if not gzip.decompress(raw).startswith(b'070701'):
            raise ValueError('Initrd must be a gzip newc archive')
        if getattr(args, 'devices', False):
            raise ValueError('--devices uses embedded native selftests; run it without --initrd')
        result.update(initrd_sha256=hashlib.sha256(raw).hexdigest(), initrd_size=len(raw), initrd_load_address=hex(INITRD_LOAD))
    return result


@contextmanager
def server(board, args, file, name):
    """Use the existing server under the explicitly chosen allowed interpreter."""
    log_path = board.op.path / (name + '-tftp.log')
    with log_path.open('wb') as log:
        process = subprocess.Popen([args.tftp_python, '-u', str(ROOT/'scripts/bios_tftp.py'),
            '--bind', args.host_ip, '--file', str(file.resolve()), '--name', name],
            stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
        try:
            deadline = time.monotonic() + 5
            while b'TFTP ready' not in log_path.read_bytes():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('TFTP server not ready: '+log_path.read_text(errors='replace'))
                time.sleep(.05)
            yield
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)


def verify(board, args):
    board.op.record('linux_inputs', **preflight(args))
    with board.connect() as port, (board.op.path/'linux-uart.log').open('wb') as log:
        session = BootSession(port, log)

        def send(text):
            for byte in (text+'\r').encode():
                port.write(bytes([byte]))
                time.sleep(.01)

        def until(marker, timeout=None):
            data = bytearray()
            deadline = time.monotonic() + (timeout or args.timeout)
            while time.monotonic() < deadline:
                chunk = port.read(port.in_waiting or 1)
                if not chunk:
                    continue
                data.extend(chunk)
                session.note(chunk)
                print(chunk.decode(errors='replace'), end='', flush=True)
                failure = b'LINUX SELFTEST FAIL' if b'LINUX SELFTEST FAIL' in data else b'Kernel panic'
                if b'Ramdisk image is corrupt or invalid' in data:
                    failure = b'Ramdisk image is corrupt or invalid'
                if failure in data and b'\n' in data[data.index(failure):]:
                    board.op.record('linux_test_failure',passed=False,
                        successful_checks=[line for line in data.decode(errors='replace').splitlines()
                                           if line.startswith('SELFTEST ') and line.endswith(' PASS')],
                        output=data.decode(errors='replace'))
                    raise RuntimeError('Linux boot/selftest failed: '+
                        data[data.index(failure):].decode(errors='replace').splitlines()[0])
                if marker in data:
                    return bytes(data)
            raise TimeoutError('Timed out waiting for '+repr(marker)+'; see linux-uart.log')

        send('')
        until(b'> ', 10)
        send('set server '+args.host_ip)
        until(b'> ', 10)
        with server(board, args, args.os_image, 'LINUX.OSB'):
            send('boot net LINUX.OSB')
            until(b'Hit any key to stop autoboot')
            port.write(b' ')
            until(b'riscv-mini> ', 15)
        for command in ('setenv stdout serial', 'setenv stderr serial',
                        'setenv ipaddr 169.254.20.20', 'setenv serverip '+args.host_ip,
                        'setenv netmask 255.255.0.0', 'setenv tftpblocksize 1428'):
            send(command)
            until(b'riscv-mini> ', 15)
        with server(board, args, args.kernel, 'Image'):
            send('tftpboot %x Image' % LOAD)
            reply = until(b'riscv-mini> ')
            if ('Bytes transferred = %d' % args.kernel.stat().st_size).encode() not in reply:
                raise RuntimeError('Kernel TFTP length mismatch')
        import zlib
        send('crc32 %x %x' % (LOAD, args.kernel.stat().st_size))
        reply = until(b'riscv-mini> ', 30)
        if ('==> %08x' % zlib.crc32(args.kernel.read_bytes())).encode() not in reply.lower():
            raise RuntimeError('Loaded kernel CRC mismatch')
        if getattr(args, 'initrd', None):
            with server(board, args, args.initrd, 'initrd.cpio.gz'):
                send('tftpboot %x initrd.cpio.gz' % INITRD_LOAD)
                reply = until(b'riscv-mini> ')
                if ('Bytes transferred = %d' % args.initrd.stat().st_size).encode() not in reply:
                    raise RuntimeError('Initrd TFTP length mismatch')
            send('crc32 %x %x' % (INITRD_LOAD, args.initrd.stat().st_size))
            reply = until(b'riscv-mini> ', 30)
            if ('==> %08x' % zlib.crc32(args.initrd.read_bytes())).encode() not in reply.lower():
                raise RuntimeError('Loaded initrd CRC mismatch')
            send('booti %x %x:%x 1080000' % (LOAD, INITRD_LOAD, args.initrd.stat().st_size))
            boot = until(b'mini-linux# ')
            if b'BUSYBOX INITRD READY' not in boot:
                raise RuntimeError('BusyBox initrd did not initialize')
            replies = {}
            commands = (
                'busybox | head -n 1', 'uname -a', 'cat /proc/1/comm',
                'mount', 'ls /dev/fb* /dev/mmcblk* /dev/snd/*',
                'ps', 'free',
                'ps -o pid,tty,args', 'cat /sys/class/tty/tty0/active',
                "script -q -c 'tty; echo PTY_OK' /tmp/pty-check; rm /tmp/pty-check",
                'base64 /dev/vcs1',
                'cat /proc/bus/input/devices', 'ls /sys/bus/usb/devices',
                'dmesg | tail -n 60',
                "sh -c 'printf pipeline | cat; echo; sleep 1 & wait; echo PROCESS_OK'",
                "dd if=/dev/zero of=/tmp/check bs=4096 count=16 && sha256sum /tmp/check && rm /tmp/check",
                'ip link set lo up; ping -c 2 127.0.0.1',
                "test -d /proc/1 && test -d /sys/class && test -c /dev/ttyLXU0 && echo ROOTFS_OK",
            )
            for command in commands:
                send(command)
                replies[command] = until(b'mini-linux# ', 45).decode(errors='replace')
            combined = '\n'.join(replies.values())
            digest = hashlib.sha256(bytes(65536)).hexdigest()
            for marker in ('BusyBox v1.37.0', 'PROCESS_OK\r\n', 'ROOTFS_OK\r\n', digest, ', 0% packet loss'):
                if marker not in combined:
                    raise RuntimeError('BusyBox acceptance missing: '+marker)
            if '\r\ninit\r\n' not in replies['cat /proc/1/comm']:
                raise RuntimeError('BusyBox init is not PID 1')
            if 'devpts on /dev/pts' not in replies['mount'] or '/dev/pts/0' not in combined or not any(line.strip()=='PTY_OK' for line in combined.splitlines()):
                raise RuntimeError('PTY allocation/child controlling terminal acceptance missing')
            import base64, re
            lcd_shell = re.search(r'^\s*\d+\s+(?:tty1|4,1)\s+/bin/sh\s+-l\s*$',
                                  replies['ps -o pid,tty,args'], re.MULTILINE)
            if not lcd_shell or '\r\ntty1\r\n' not in replies['cat /sys/class/tty/tty0/active']:
                raise RuntimeError('LCD virtual terminal shell is missing or inactive')
            encoded = ''.join(line.strip() for line in replies['base64 /dev/vcs1'].splitlines()
                              if re.fullmatch(r'[A-Za-z0-9+/=]+', line.strip()))
            lcd_text = base64.b64decode(encoded,validate=True).decode(errors='replace')
            # Interactive commands can scroll the startup banner off screen.
            # The active VT, controlling tty and visible prompt qualify the shell.
            if 'mini-linux# ' not in lcd_text:
                raise RuntimeError('LCD console text lacks its shell prompt')
            board.op.record('linux_lcd_shell',passed=True,active_vt='tty1',pty=True,
                            console_text=lcd_text,physical_observation=False)
            input_devices = replies['cat /proc/bus/input/devices']
            usb_keyboard = any('Bus=0003' in device and re.search(r'Handlers=.*\bkbd\b',device)
                               for device in input_devices.split('\r\n\r\n'))
            board.op.record('linux_usb_keyboard',passed=usb_keyboard,
                            input_devices=input_devices,kernel_log=replies['dmesg | tail -n 60'],
                            physical_input_verified=False)
            board.op.record('linux_busybox', passed=True, commands=replies, uart_log=str(board.op.path/'linux-uart.log'),
                            state='BusyBox init and interactive ash running in DDR; Flash/SD unchanged')
            print('\nBUSYBOX BOARD PASS', flush=True)
            return
        send('booti %x - 1080000' % LOAD)
        boot = until(b'linux-mini> ')
        checks = (b'Linux version 6.12.112', b'riscv-mini Linux init pid=1',
                  b'SELFTEST mmap/mprotect/munmap PASS', b'SELFTEST fork/exec/wait PASS',
                  b'SELFTEST timer/nanosleep PASS', b'LINUX USERSPACE PASS')
        checks += (b'SELFTEST fb0 RGB565/mmap/readback/DMA/blank PASS',)
        if any(check not in boot for check in checks):
            raise RuntimeError('Linux userspace acceptance marker missing')
        replies = {}
        for command in ('uname', 'cpu', 'mem', 'uptime', 'test', 'fb', 'fbstat'):
            send(command)
            replies[command] = until(b'linux-mini> ', 30).decode(errors='replace')
        if 'LINUX USERSPACE PASS' not in replies['test']:
            raise RuntimeError('Repeated Linux selftest did not pass')
        if 'SELFTEST fb0 RGB565/mmap/readback/DMA/blank PASS' not in replies['fb']:
            raise RuntimeError('Repeated framebuffer selftest did not pass')
        if getattr(args, 'devices', False):
            with echo_server(board, args) as peer_port:
                send('network '+args.host_ip+' '+str(peer_port))
                replies['network'] = until(b'linux-mini> ', 45).decode(errors='replace')
            if 'SELFTEST Ethernet-IRQ/ring-DMA/IPv4/UDP-32x1000 PASS' not in replies['network']:
                raise RuntimeError('Linux Ethernet round-trip acceptance missing')
            send('devices')
            replies['devices'] = until(b'linux-mini> ', 90).decode(errors='replace')
            if 'LINUX DEVICES PASS' not in replies['devices']:
                raise RuntimeError('Native Linux device acceptance missing')
            board.op.record('linux_devices', passed=True, commands={key:replies[key] for key in ('devices','network')},
                            storage_writes=False, physical_observation=False)
        board.op.record('linux_userspace', passed=True, commands=replies,
                        uart_log=str(board.op.path/'linux-uart.log'),
                        state='Linux init console running in DDR; Flash unchanged')
        print('\nLINUX BOARD PASS', flush=True)


@contextmanager
def echo_server(board, args):
    path = board.op.path/'linux-udp-echo.log'
    with path.open('wb') as log:
        process = subprocess.Popen([args.tftp_python, '-u', str(ROOT/'scripts/linux_echo.py'),
            '--bind', args.host_ip], stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
        try:
            deadline = time.monotonic()+5
            while b'UDP echo ready' not in path.read_bytes():
                if process.poll() is not None or time.monotonic()>deadline:
                    raise RuntimeError('UDP echo startup failed: '+path.read_text(errors='replace'))
                time.sleep(.05)
            yield int(path.read_text().splitlines()[0].split()[-1])
        finally:
            if process.poll() is None: process.terminate()
            process.wait(timeout=5)
