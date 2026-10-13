"""Check the libc semantics used by DOOM's wall projection on RV32."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import doom_sim
TOOLS = ROOT / '.tools.local.json'
CC = json.loads(TOOLS.read_text())['gcc'] if TOOLS.exists() else None
QEMU = doom_sim.find_qemu()


@unittest.skipUnless(QEMU and CC, 'configured RISC-V compiler and QEMU required')
class WallAngleTest(unittest.TestCase):
    def test_unsigned_angle_difference_uses_signed_abs_argument(self):
        # R_StoreWallRange passes an angle_t (unsigned) subtraction to abs(int),
        # then clips its magnitude to ANG90. Test both directions and wraparound.
        source = r'''
#include "stdlib.h"
__asm__(".section .text.entry,\"ax\"\n.globl _start\n_start:\nli sp,0x80100000\ncall main\n"
        "slli a0,a0,16\nli t0,0x3333\nbnez a0,1f\nli t0,0x5555\n"
        "1: or a0,a0,t0\nli t0,0x100000\nsw a0,0(t0)\n2: j 2b\n.text\n");
typedef unsigned int angle_t;
static angle_t offset(angle_t normal, angle_t ray) {
    angle_t result = abs(normal - ray);
    if (result > 0x40000000u) result = 0x40000000u;
    return result;
}
int main(void) {
    volatile angle_t normal = 0x20000000u, ray = 0x30000000u;
    if (offset(normal, ray) != 0x10000000u) return 1;
    if (offset(ray, normal) != 0x10000000u) return 2;
    if (offset(0x08000000u, 0xf8000000u) != 0x10000000u) return 3;
    if (offset(0xf8000000u, 0x08000000u) != 0x10000000u) return 4;
    if (offset(0, 0x60000000u) != 0x40000000u) return 5;
    if (abs(-17) != 17 || abs(17) != 17 || abs(0) != 0) return 6;
    int value = -3;
    if (abs(value++) != 3 || value != -2) return 7;
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='doom-shim-') as directory:
            path = Path(directory)
            env = dict(os.environ, TMP=str(path), TEMP=str(path), TMPDIR=str(path))
            c = path / 'wall_angle.c'
            exe = path / 'wall_angle.elf'
            c.write_text(source)
            linker = path / 'test.ld'
            linker.write_text('ENTRY(_start)\nSECTIONS { . = 0x80000000; '
                              '.text : { *(.text.entry) *(.text*) } '
                              '.data : { *(.data*) } .bss : { *(.bss*) } }')
            for optimization in ('-O0', '-O2'):
                with self.subTest(optimization=optimization):
                    compiled = subprocess.run([CC, optimization, '-march=rv32im', '-mabi=ilp32',
                                    '-ffreestanding', '-nostdlib', '-fno-builtin',
                                    '-Wl,--no-relax', '-T', str(linker),
                                    str(c), '-lc', '-lgcc', '-o', str(exe)],
                                   capture_output=True, text=True, env=env)
                    self.assertEqual(compiled.returncode, 0, compiled.stderr)
                    result = subprocess.run([str(QEMU), '-machine', 'virt', '-bios',
                                             'none', '-nographic', '-kernel', str(exe)],
                                            capture_output=True, timeout=15)
                    self.assertEqual(result.returncode, 0,
                                     f'wall angle / abs case {result.returncode} failed')


if __name__ == '__main__':
    unittest.main()
