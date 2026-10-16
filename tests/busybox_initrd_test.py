"""Prevent packaging a host executable or unusable PID 1 into the initrd."""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.builders.busybox import validate_archive


def elf(machine=243, bits=1):
    data = bytearray(52)
    data[:6] = b'\x7fELF'+bytes((bits,1))
    struct.pack_into('<H', data, 18, machine)
    return bytes(data)


def archive(binary, init_mode=0o100755):
    data = bytearray()
    files = [('init',init_mode,b'#!/bin/sh\n'), ('bin/busybox',0o100755,binary),
             ('bin/sh',0o120777,b'busybox'), ('sbin/init',0o120777,b'../bin/busybox'),
             ('etc/inittab',0o100644,b'ttyLXU0::respawn:/bin/sh\n'),
             ('dev/console',0o20600,b''), ('TRAILER!!!',0,b'')]
    for name, mode, payload in files:
        encoded = name.encode()+b'\0'
        fields = [1,mode,0,0,1,0,len(payload),0,0,5,1,len(encoded),0]
        data += b'070701'+''.join('%08x'%v for v in fields).encode()+encoded
        data += bytes((-len(data))%4)
        data += payload
        data += bytes((-len(data))%4)
    return bytes(data)


class BusyBoxArchiveTests(unittest.TestCase):
    def test_accepts_rv32_rootfs(self):
        self.assertEqual(validate_archive(archive(elf()),elf()),6)

    def test_rejects_install_rebuild_for_host(self):
        with self.assertRaisesRegex(ValueError, 'differs'):
            validate_archive(archive(elf(machine=62,bits=2)),elf())

    def test_rejects_host_binary_even_when_artifact_matches(self):
        host = elf(machine=62,bits=2)
        with self.assertRaisesRegex(ValueError, 'ELF32 RISC-V'):
            validate_archive(archive(host),host)

    def test_rejects_non_executable_init(self):
        with self.assertRaisesRegex(ValueError, 'executable'):
            validate_archive(archive(elf(),0o100644),elf())


if __name__ == '__main__':
    unittest.main()
