"""Run the GW-BASIC payload on QEMU with a shim that speaks the BIOS ecall ABI.

The interpreter is bare metal and only talks to the board through the resident
BIOS ecall interface, so a small shim can stand in for the BIOS: it provides the
bios_info structure, services the ecalls from the 16550 UART and the test
finisher of the QEMU virt machine, and catches traps so a crash is reported
instead of silently hanging.

The payload objects are the ones scripts/gwbasic_image.py builds for the board.
They are relinked at 0x80000000 here because that is where the QEMU virt machine
puts RAM, and the payload is not position independent (the board link address is
0x01000000).  The code paths and the compiled code are the same; only the
address constants differ.  The shipped BOOT.RPB image itself is checked by
tests/gwbasic_test.py.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'firmware/apps/gwbasic'
HELPERS = [ROOT / 'firmware/drivers/string.c']
RAM_BASE = 0x80000000
FINISHER = 0x00100000
UART0 = 0x10000000

SHIM = r'''
/* Stand-in for the resident BIOS: provide bios_info, serve the ecalls from the
 * UART and the test finisher, and report traps. */
#include "firmware/bios/include/bios.h"

#define UART0_BASE 0x10000000u
#define FINISHER   0x00100000u
#define MAGIC      0x42494f53u

static struct bios_info sim_info;
static unsigned short fb0[480 * 272];
static unsigned short fb1[480 * 272];
static unsigned int trap_count;

void sim_finish(long code) __attribute__((noreturn));
void sim_finish(long code)
{
    *(volatile unsigned int *)FINISHER = code ? 0x3333u : 0x5555u;
    for (;;)
        ;
}

static void uart_putc(int c)
{
    while (!(*(volatile unsigned char *)(UART0_BASE + 5) & 0x20))
        ;
    *(volatile unsigned char *)UART0_BASE = (unsigned char)c;
}

static void uart_puts(const char *s)
{
    while (*s)
        uart_putc(*s++);
}

static void uart_hex(unsigned int v)
{
    static const char digits[] = "0123456789abcdef";

    uart_puts("0x");
    for (int shift = 28; shift >= 0; shift -= 4)
        uart_putc(digits[(v >> shift) & 15]);
}

static int uart_getc(void)
{
    if (!(*(volatile unsigned char *)(UART0_BASE + 5) & 0x01))
        return -1;
    return *(volatile unsigned char *)UART0_BASE;
}

/* QEMU semihosting: only used for file reads, which the UART cannot carry. */
static long semihost(int op, void *arg)
{
    register long a0 asm("a0") = op;
    register void *a1 asm("a1") = arg;

    asm volatile(".option push\n.option norelax\n"
                 "slli zero,zero,0x1f\n"
                 "ebreak\n"
                 "srai zero,zero,0x1f\n"
                 ".option pop\n"
                 : "+r"(a0) : "r"(a1) : "memory");
    return a0;
}

static long host_open(const char *name)
{
    long block[3];

    block[0] = (long)name;
    block[1] = 0;                  /* mode: read */
    block[2] = 0;
    return semihost(0x01, block);
}

static long host_read(long fd, unsigned char *dst, long len)
{
    long block[3];

    block[0] = fd;
    block[1] = (long)dst;
    block[2] = len;
    return semihost(0x06, block);
}

static void host_close(long fd)
{
    long block[1];

    block[0] = fd;
    (void)semihost(0x02, block);
}

static int sim_file_read(const char *name, unsigned offset, unsigned char *dst,
                         unsigned capacity)
{
    long fd = host_open(name);
    long got;

    if (fd < 0)
        return -1;
    if (offset) {
        /* semihosting has no seek op the shim needs: read and discard */
        unsigned char scratch[64];
        unsigned left = offset;

        while (left) {
            long step = left > sizeof(scratch) ? (long)sizeof(scratch) : (long)left;

            if (host_read(fd, scratch, step) != step) {
                host_close(fd);
                return -1;
            }
            left -= (unsigned)step;
        }
    }
    got = host_read(fd, dst, (long)capacity);
    host_close(fd);
    return got < 0 ? -1 : (int)got;
}

/* Called from the trap handler with the saved register frame. */
void sim_trap(unsigned int *f)
{
    unsigned int a7 = f[17], a6 = f[16], a0 = f[10], a1 = f[11];
    unsigned int cause = f[19];

    if (cause != 11) {             /* not an M-mode ecall: report and stop */
        uart_puts("\r\nSIM TRAP cause=");
        uart_hex(cause);
        uart_puts(" mepc=");
        uart_hex(f[18]);
        uart_puts("\r\n");
        sim_finish(1);
    }
    f[18] += 4;                    /* ecall: resume after it */
    if (a7 != MAGIC) {
        f[10] = (unsigned int)-1;
        return;
    }
    switch (a6) {
    case BIOS_INFO:                /* a0 = destination */
        *((struct bios_info *)a0) = sim_info;
        f[10] = 0;
        break;
    case BIOS_WRITE:
        for (unsigned i = 0; i < a1; i++)
            uart_putc(((const char *)a0)[i]);
        f[10] = a1;
        break;
    case BIOS_GETC:
        f[10] = (unsigned int)uart_getc();
        break;
    case BIOS_TIME:
        f[10] = ++trap_count * 5;  /* a deterministic clock */
        break;
    case BIOS_POLL:
        f[10] = 0;
        break;
    case BIOS_VIDEO_MODE:
        f[10] = 0;                 /* video is always available here */
        break;
    case BIOS_VIDEO_PRESENT:
        f[10] = 0;
        break;
    case BIOS_FILE_READ: {
        long got = sim_file_read((const char *)a0, a1, (unsigned char *)f[12], f[13]);

        f[10] = (unsigned int)got;
        break;
    }
    case BIOS_REBOOT:
        sim_finish(0);
        break;
    default:
        f[10] = (unsigned int)-1;
        break;
    }
}

__attribute__((naked, used, section(".text.entry"))) void _start(void)
{
    __asm__ volatile(
        "la sp, __stack_top\n"
        "la t0, _file_end\n"
        "la t1, _memory_end\n"
        "1:\n"
        "bgeu t0, t1, 2f\n"
        "sw zero, 0(t0)\n"
        "addi t0, t0, 4\n"
        "j 1b\n"
        "2:\n"
        "la t0, sim_trap_handler\n"     /* mtvec, direct mode */
        "csrw mtvec, t0\n"
        /* The board's firmware/boot/start.S sets mstatus.FS=Dirty when the
         * build has an FPU; the payload inherits it and may use float.  A
         * payload that does not get it traps on its first FP instruction. */
        "li t0, 0x6000\n"
        "csrs mstatus, t0\n"
        "call sim_setup\n"
        "la a0, sim_info\n"
        "call _payload_start\n"
        "li a0, 0\n"
        "call sim_finish\n");
}

void sim_setup(void)
{
    sim_info.version = BIOS_ABI_VERSION;
    sim_info.size = sizeof(sim_info);
    sim_info.ram_base = 0x80000000u;
    sim_info.ram_bytes = 0x10000000u;
    sim_info.payload_base = 0x80000000u;
    sim_info.payload_limit = 0x90000000u;
    sim_info.framebuffer[0] = (uintptr_t)fb0;
    sim_info.framebuffer[1] = (uintptr_t)fb1;
    sim_info.width = 480;
    sim_info.height = 272;
    sim_info.stride = 960;
    sim_info.format = 565;
    sim_info.features = 0x1fu;
    sim_info.clock_hz = 60000000u;
}

__attribute__((naked, used)) void sim_trap_handler(void)
{
    __asm__ volatile(
        "addi sp, sp, -128\n"
        "sw ra, 0(sp)\nsw gp, 4(sp)\nsw tp, 8(sp)\n"
        "sw t0, 12(sp)\nsw t1, 16(sp)\nsw t2, 20(sp)\nsw t3, 24(sp)\n"
        "sw t4, 28(sp)\nsw t5, 32(sp)\nsw t6, 36(sp)\n"
        "sw a0, 40(sp)\nsw a1, 44(sp)\nsw a2, 48(sp)\nsw a3, 52(sp)\n"
        "sw a4, 56(sp)\nsw a5, 60(sp)\nsw a6, 64(sp)\nsw a7, 68(sp)\n"
        "csrr t0, mepc\nsw t0, 72(sp)\n"
        "csrr t0, mcause\nsw t0, 76(sp)\n"
        "mv a0, sp\ncall sim_trap\n"
        "lw t0, 72(sp)\ncsrw mepc, t0\n"
        "lw ra, 0(sp)\nlw gp, 4(sp)\nlw tp, 8(sp)\n"
        "lw t0, 12(sp)\nlw t1, 16(sp)\nlw t2, 20(sp)\nlw t3, 24(sp)\n"
        "lw t4, 28(sp)\nlw t5, 32(sp)\nlw t6, 36(sp)\n"
        "lw a0, 40(sp)\nlw a1, 44(sp)\nlw a2, 48(sp)\nlw a3, 52(sp)\n"
        "lw a4, 56(sp)\nlw a5, 60(sp)\nlw a6, 64(sp)\nlw a7, 68(sp)\n"
        "addi sp, sp, 128\nmret\n");
}
'''

LINKER = '''
OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY { RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 256M }
SECTIONS {
    .text : { KEEP(*(.text.entry)) KEEP(*(.text.start)) *(.text .text.*) } > RAM
    .rodata : { *(.rodata .rodata.*) } > RAM
    .data : { . = ALIGN(4); *(.data .data.* .sdata .sdata.*) . = ALIGN(4); } > RAM
    __global_pointer$ = . + 0x800;
    _file_end = .;
    .bss (NOLOAD) : { . = ALIGN(4); *(.bss .bss.* .sbss .sbss.*) *(COMMON) . = ALIGN(4); } > RAM
    _memory_end = .;
    .stack (NOLOAD) : { . = ALIGN(16); . += 64K; __stack_top = .; } > RAM
    /DISCARD/ : { *(.comment) *(.eh_frame*) }
}
'''


def find_qemu(explicit=None):
    if explicit:
        return Path(explicit)
    if os.environ.get('QEMU_RISCV32'):
        return Path(os.environ['QEMU_RISCV32'])
    found = shutil.which('qemu-system-riscv32')
    if found:
        return Path(found)
    for guess in ('C:/msys64/ucrt64/bin/qemu-system-riscv32.exe',
                  'C:/msys64/mingw64/bin/qemu-system-riscv32.exe'):
        if Path(guess).is_file():
            return Path(guess)
    return None


def build(out, soft_float=False):
    gcc = Path(json.loads((ROOT / '.tools.local.json').read_text())['gcc'])
    scratch = out / 'tmp'
    scratch.mkdir(parents=True, exist_ok=True)
    for name in ('TMP', 'TEMP', 'TMPDIR'):
        os.environ[name] = str(scratch)
    (out / 'shim.c').write_text(SHIM, encoding='utf-8')
    (out / 'sim.ld').write_text(LINKER, encoding='utf-8')
    march, mabi = (('rv32im_zicsr_zifencei', 'ilp32') if soft_float
                   else ('rv32imaf_zicsr_zifencei', 'ilp32f'))
    sources = sorted([p for p in APP.iterdir() if p.suffix in ('.c', '.S')])
    elf = out / 'sim.elf'
    args = [str(gcc), '-march=' + march, '-mabi=' + mabi, '-Os', '-Wall', '-Wextra',
            '-ffreestanding', '-fno-builtin', '-ffunction-sections', '-fdata-sections',
            '-msmall-data-limit=0', '-nostdlib', '-nostartfiles', '-I', str(ROOT),
            str(ROOT / 'firmware/bios/payload_start.S'),
            str(out / 'shim.c'), *[str(p) for p in sources],
            *[str(p) for p in HELPERS],
            '-T', str(out / 'sim.ld'), '-Wl,--gc-sections',
            '-Wl,-Map,' + str(out / 'sim.map'), '-lgcc', '-o', str(elf)]
    subprocess.run(args, check=True)
    return elf


def run(elf, qemu, script, timeout, log_path=None, soft_float=False, cpu='rv32',
        char_delay=0.002, pace=True):
    cmd = [str(qemu), '-machine', 'virt', '-m', '256', '-nographic', '-bios', 'none',
           '-kernel', str(elf), '-semihosting-config', 'enable=on,target=native',
           '-cpu', cpu, '-no-reboot']
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    collected = bytearray()
    state = {'timed_out': False}

    def feed():
        """Type the script at the console.

        The 16550 in front of the guest has a 16 byte FIFO, so a whole script
        written at once is dropped on the floor: characters have to be paced.
        Between lines the feeder waits for the console to fall quiet, which is
        how it knows the interpreter has finished with the line it was given.
        """
        lines = script.splitlines(keepends=True)
        try:
            for line in lines:
                for ch in line:
                    proc.stdin.write(bytes([ch]))
                    proc.stdin.flush()
                    if char_delay:
                        time.sleep(char_delay)
                if not pace:
                    continue
                deadline = time.monotonic() + 15.0
                seen = len(collected)
                quiet = 0
                while time.monotonic() < deadline and quiet < 3:
                    if proc.poll() is not None:
                        return
                    time.sleep(0.05)
                    if len(collected) != seen:
                        seen = len(collected)
                        quiet = 0
                    else:
                        quiet += 1
        except (BrokenPipeError, OSError):
            pass

    def watchdog():
        # A payload stuck in a loop produces no output, so the read below would
        # block forever: the timer has to be able to kill QEMU from the side.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return
            time.sleep(0.2)
        state['timed_out'] = True
        try:
            proc.kill()
        except OSError:
            pass

    import threading
    threading.Thread(target=feed, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    while True:
        chunk = proc.stdout.read(1)
        if not chunk:
            break
        collected += chunk
    proc.wait()
    for stream in (proc.stdin, proc.stdout):
        try:
            stream.close()
        except OSError:
            pass
    text = collected.decode(errors='replace')
    if log_path:
        log_path.write_text(text, encoding='utf-8', errors='replace')
    return proc.returncode, text, state['timed_out']


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir', type=Path, default=ROOT / 'build/gwbasic-sim')
    p.add_argument('--qemu', type=Path)
    p.add_argument('--script', type=Path, help='file of BASIC lines to type at the prompt')
    p.add_argument('--script-text', help='the same, given on the command line')
    p.add_argument('--timeout', type=float, default=120.0)
    p.add_argument('--expect', action='append', default=[],
                   help='substring that must appear; may be repeated')
    p.add_argument('--soft-float', action='store_true')
    p.add_argument('--cpu', default='rv32', help='QEMU -cpu value')
    p.add_argument('--keep-going', action='store_true',
                   help='do not append SYSTEM to the script')
    a = p.parse_args()
    qemu = find_qemu(a.qemu)
    if not qemu:
        raise SystemExit('qemu-system-riscv32 not found; pass --qemu or set QEMU_RISCV32')
    out = a.output_dir
    out.mkdir(parents=True, exist_ok=True)
    elf = build(out, a.soft_float)
    script = b''
    if a.script:
        script = a.script.read_bytes()
    elif a.script_text:
        script = a.script_text.encode()
    if script and not script.endswith(b'\n'):
        script += b'\n'
    if not a.keep_going:
        script += b'SYSTEM\n'
    code, text, timed_out = run(elf, qemu, script, a.timeout, out / 'sim.log', a.soft_float,
                                a.cpu)
    sys.stdout.write(text)
    if timed_out:
        print('\nSIM TIMEOUT after %.0fs' % a.timeout, flush=True)
        return 1
    missing = [want for want in a.expect if want not in text]
    if missing:
        print('\nMISSING: ' + ', '.join(repr(m) for m in missing), flush=True)
        return 1
    print('\nQEMU exit code %s' % code, flush=True)
    return 0 if code == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
