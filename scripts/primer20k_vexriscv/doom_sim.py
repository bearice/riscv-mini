"""Run the DOOM payload on QEMU with a shim that speaks the BIOS ecall ABI.

The payload sources are the ones mini build doom ships to the board: they
only ever talk to the board through the resident BIOS ecall interface, so a small
shim can stand in for the BIOS (bios_info, console, panel, millisecond clock, SD
file reads, raw USB HID key events from a script).  The objects are relinked at
0x80000000 because that is where the QEMU virt machine puts RAM while the board
link address is 0x01000000; the payload is not position independent, so only
address constants differ.  Code paths and compiled code are identical.  The
shipped BOOT.RPB image itself is verified on real hardware.

Files: DOOM1.WAD is linked into the image as a read-only blob, so the run needs
no host file access from the guest (QEMU semihosting is not required and is not
used).  Frames: the harness dumps both panel slots straight out of guest RAM
through the QEMU monitor and converts them to PNG, so a run that never renders
is obvious.
"""
import sys
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[2] / 'scripts')]

import argparse
import json
import os
import shutil
import socket
import struct
import subprocess
import threading
import time
import zlib
from pathlib import Path
from scripts.primer20k_vexriscv.payload_libc import newlib_link
from scripts.tools.riscv_toolchain import executable as tool_executable

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'firmware/apps/doom'
HELPERS = [ROOT / 'firmware/libc/runtime.c']
WAD_BLOB = 'wad.bin'          # copied into the output dir, linked as binary
PANEL_BYTES = 480 * 272 * 2

SHIM = r'''
/* Stand-in for the resident BIOS: bios_info, the ecall services DOOM uses, raw
 * HID key events from a scripted queue, and the linked WAD as the "SD card". */
#include "firmware/bios/include/bios.h"
#include <string.h>

#define UART0_BASE 0x10000000u
#define FINISHER   0x00100000u
#define MAGIC      0x42494f53u

#ifndef SIM_MAX_PRESENTS
#define SIM_MAX_PRESENTS 100000
#endif

extern const unsigned char _binary_wad_bin_start[];
extern const unsigned char _binary_wad_bin_end[];

static struct bios_info sim_info;
static unsigned short panel[2][480 * 272];
static unsigned long sim_ms;
static unsigned long presents;
static unsigned long key_index;

/* WAD plus one bounded writable fixture; absent files really fail. */
struct sim_file {int used, kind, flags; unsigned long pos;};
static struct sim_file sim_files[6];
static unsigned char test_data[16384];
static unsigned test_size;
static int test_exists, directory_exists;
static char test_name[64], directory_name[64];
static unsigned long wad_len(void) {return _binary_wad_bin_end - _binary_wad_bin_start;}
static int sim_handle_ok(unsigned h) {return h<6 && sim_files[h].used;}
static int file_kind(const char *name) {
    if (!strcmp(name,"DOOM1.WAD")) return 0;
    if (test_exists && !strcmp(name,test_name)) return 1;
    if (directory_exists && !strcmp(name,directory_name)) return 2;
    return -1;
}
static unsigned long file_size(unsigned h) {return sim_files[h].kind ? test_size : wad_len();}

/* Scripted input: time in simulated ms, HID usage, pressed flag. */
struct sim_key {unsigned long at_ms; unsigned char usage, pressed;};
#ifdef SIM_NOKEYS
/* --define SIM_NOKEYS injects nothing, so the payload is left alone in the
 * attract loop (title page / demo / credit page) exactly like a board with no
 * keyboard attached.  The default script opens the menu, which paints over the
 * very pages those runs are meant to observe. */
static const struct sim_key keys[1];
#define SIM_KEYS 0u
#else
static const struct sim_key keys[] = {
    {1200, 0x28, 1}, {1300, 0x28, 0},   /* ENTER: title screen -> menu      */
    {1600, 0x28, 1}, {1700, 0x28, 0},   /* ENTER: New Game                  */
    {1900, 0x28, 1}, {2000, 0x28, 0},   /* ENTER: skill (Hurt me plenty)    */
    {3000, 0x52, 1}, {4200, 0x52, 0},   /* UP arrow: walk forward           */
    {4400, 0x50, 1}, {4800, 0x50, 0},   /* LEFT arrow: turn                 */
    {5000, 0x4f, 1}, {5400, 0x4f, 0},   /* RIGHT arrow: turn back           */
    {5600, 0xe0, 1}, {6000, 0xe0, 0},   /* Ctrl: fire                       */
    {6200, 0x2c, 1}, {6300, 0x2c, 0},   /* SPACE: use/open                  */
};
#define SIM_KEYS (sizeof keys / sizeof keys[0])
#endif

/* Scripted UART characters for the payload's debug console (dg_debug.c): each
 * becomes readable once the simulated clock reaches its time.  This is how the
 * pause/dump path is exercised without a board. */
struct sim_uart {unsigned long at_ms; unsigned char c;};
static const struct sim_uart uart_in[] = {SIM_UART_SCRIPT};
static const unsigned sim_uarts = SIM_UART_COUNT;
static unsigned uart_index;

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

static void uart_puts(const char *s) { while (*s) uart_putc(*s++); }
static void uart_hex(unsigned int v)
{
    static const char digits[] = "0123456789abcdef";
    uart_puts("0x");
    for (int shift = 28; shift >= 0; shift -= 4)
        uart_putc(digits[(v >> shift) & 15]);
}
static void uart_dec(unsigned long v)
{
    char b[24]; int n = 0;
    do { b[n++] = (char)('0' + v % 10); v /= 10; } while (v);
    while (n) uart_putc(b[--n]);
}

void sim_trap(unsigned int *f)
{
    unsigned int a7 = f[17], a6 = f[16], a0 = f[10], a1 = f[11];
    unsigned int cause = f[19];

    if (cause != 11) {
        uart_puts("\r\nSIM TRAP cause=");
        uart_hex(cause);
        uart_puts(" mepc=");
        uart_hex(f[18]);
        uart_puts(" mtval=");
        uart_hex(f[20]);
        uart_puts("\r\n");
        sim_finish(1);
    }
    f[18] += 4;
    if (a7 != MAGIC) { f[10] = (unsigned int)-1; return; }
    switch (a6) {
    case BIOS_INFO:
        sim_info.framebuffer[0] = (uintptr_t)panel[0];
        sim_info.framebuffer[1] = (uintptr_t)panel[1];
        *((struct bios_info *)a0) = sim_info;
        f[10] = 0;
        break;
    case BIOS_WRITE:
        for (unsigned i = 0; i < a1; i++) uart_putc(((const char *)a0)[i]);
        f[10] = a1;
        break;
    case BIOS_GETC:
        if (uart_index < sim_uarts && uart_in[uart_index].at_ms <= sim_ms)
            f[10] = uart_in[uart_index++].c;
        else
            f[10] = (unsigned int)-1;
        break;
    case BIOS_TIME:
        f[10] = (unsigned int)sim_ms;
        ++sim_ms;                   /* deterministic clock */
        break;
    case BIOS_POLL:
        f[10] = 0;
        break;
    case BIOS_VIDEO_MODE:
        f[10] = 0;
        break;
    case BIOS_VIDEO_PRESENT:
        if (a0 < 2) {
            ++presents;
            if (presents % 100 == 0) {
                uart_puts("SIM: presents=");
                uart_dec(presents);
                uart_puts(" ms=");
                uart_dec(sim_ms);
                uart_puts("\r\n");
            }
            if (presents >= SIM_MAX_PRESENTS) {
                uart_puts("\r\nSIM: reached present limit\r\n");
                sim_finish(0);
            }
            f[10] = 0;
        } else f[10] = (unsigned int)-1;
        break;
    case BIOS_KEY: {
        struct bios_key *out = (struct bios_key *)a0;
        if (key_index < SIM_KEYS && keys[key_index].at_ms <= sim_ms) {
            out->usage = keys[key_index].usage;
            out->pressed = keys[key_index].pressed;
            out->modifiers = 0;
            out->time_ms = (uint32_t)sim_ms;
            ++key_index;
            f[10] = 0;
        } else f[10] = (unsigned int)-1;
        break;
    }
    case BIOS_MOUSE:
        f[10] = (unsigned int)-1;
        break;
    case BIOS_FILE_SIZE:
        f[10] = (unsigned int)(_binary_wad_bin_end - _binary_wad_bin_start);
        break;
    case BIOS_FILE_READ: {
        unsigned long size = (unsigned long)(_binary_wad_bin_end - _binary_wad_bin_start);
        unsigned long off = a1;
        if (off >= size) { f[10] = 0; break; }
        unsigned long n = f[13];              /* a3 = capacity */
        if (off + n > size) n = size - off;
        for (unsigned long i = 0; i < n; ++i)
            ((unsigned char *)f[12])[i] = _binary_wad_bin_start[off + i];
        f[10] = (unsigned int)n;
        break;
    }
    case BIOS_FS_OPEN: {
        const char *name=(const char *)a0; unsigned flags=a1;
        int kind=file_kind(name), handle=-1;
        if (kind==2 || (kind==0 && (flags&BIOS_FS_O_WRITE))) {f[10]=(unsigned)-1; break;}
        if ((flags&BIOS_FS_O_CREATE) && kind>=0 && !(flags&BIOS_FS_O_TRUNC)) {f[10]=(unsigned)-1; break;}
        if (kind<0 && (flags&(BIOS_FS_O_CREATE|BIOS_FS_O_TRUNC))) {
            if (test_exists || strlen(name)>=sizeof test_name) {f[10]=(unsigned)-1; break;}
            strcpy(test_name,name); test_exists=1; test_size=0; kind=1;
        }
        if (kind<0) {f[10]=(unsigned)-1; break;}
        if (flags&BIOS_FS_O_TRUNC) test_size=0;
        for (int i=0;i<6;++i) if (!sim_files[i].used) {
            sim_files[i].used=1; sim_files[i].kind=kind; sim_files[i].flags=flags;
            sim_files[i].pos=(flags&BIOS_FS_O_APPEND) ? (kind ? test_size : wad_len()) : 0;
            handle=i; break;
        }
        f[10]=(unsigned)handle; break;
    }
    case BIOS_FS_CLOSE:
        if(sim_handle_ok(a0)) {sim_files[a0].used=0; f[10]=0;} else f[10]=(unsigned)-1;
        break;
    case BIOS_FS_READ: {
        unsigned h=a0;
        if (!sim_handle_ok(h) || !(sim_files[h].flags&BIOS_FS_O_READ)) {f[10]=(unsigned)-1; break;}
        unsigned long size=file_size(h), pos=sim_files[h].pos;
        unsigned long left=pos<size ? size-pos : 0, n=f[12]<left ? f[12] : left;
        const unsigned char *data=sim_files[h].kind ? test_data : _binary_wad_bin_start;
        if(n)memcpy((void *)a1,data+pos,n);
        sim_files[h].pos+=n; f[10]=n; break;
    }
    case BIOS_FS_WRITE: {
        unsigned h=a0;
        if (!sim_handle_ok(h) || !sim_files[h].kind || !(sim_files[h].flags&BIOS_FS_O_WRITE)) {f[10]=(unsigned)-1; break;}
        unsigned pos=sim_files[h].pos, n=f[12];
        if (pos>=sizeof test_data) {f[10]=0; break;}
        if (n>sizeof test_data-pos)n=sizeof test_data-pos;
        memcpy(test_data+pos,(const void *)a1,n); sim_files[h].pos+=n;
        if(sim_files[h].pos>test_size)test_size=sim_files[h].pos;
        f[10]=n; break;
    }
    case BIOS_FS_SEEK: {
        unsigned h=a0;
        if (!sim_handle_ok(h)) {f[10]=(unsigned)-1; break;}
        long base=(f[12]==BIOS_FS_END)?(long)file_size(h):(f[12]==BIOS_FS_CUR)?(long)sim_files[h].pos:0;
        long target=base+(int)a1; if(target<0)target=0;
        sim_files[h].pos=target; f[10]=target; break;
    }
    case BIOS_FS_STAT: {
        int kind=file_kind((const char *)a0);
        if(kind<0) {f[10]=(unsigned)-1; break;}
        unsigned *out=(unsigned *)a1;
        out[0]=kind==0 ? wad_len() : kind==1 ? test_size : 0;
        out[1]=kind==2 ? 0x10 : 0; f[10]=0; break;
    }
    case BIOS_FS_MKDIR:
        if(directory_exists || strlen((const char *)a0)>=sizeof directory_name) f[10]=(unsigned)-1;
        else {strcpy(directory_name,(const char *)a0); directory_exists=1; f[10]=0;}
        break;
    case BIOS_FS_UNLINK:
        if(file_kind((const char *)a0)==1) {test_exists=0; f[10]=0;} else f[10]=(unsigned)-1;
        break;
    case BIOS_FS_RENAME:
        if(file_kind((const char *)a0)==1 && file_kind((const char *)a1)<0 && strlen((const char *)a1)<sizeof test_name) {
            strcpy(test_name,(const char *)a1); f[10]=0;
        } else f[10]=(unsigned)-1;
        break;
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
        "la t0, sim_trap_handler\n"
        "csrw mtvec, t0\n"
        /* The board firmware sets mstatus.FS=Dirty; DOOM uses the FPU. */
        "li t0, 0x6000\n"
        "csrs mstatus, t0\n"
        "call sim_setup\n"
        "la a0, sim_info\n"
        "call _payload_start\n"
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
    sim_info.framebuffer[0] = (uintptr_t)panel[0];
    sim_info.framebuffer[1] = (uintptr_t)panel[1];
    sim_info.width = 480;
    sim_info.height = 272;
    sim_info.stride = 960;
    sim_info.format = 565;
    sim_info.features = 0x1fu;
    sim_info.clock_hz = 60000000u;
    uart_puts("\r\nSIM: bios shim ready, wad bytes=");
    uart_dec((unsigned long)(_binary_wad_bin_end - _binary_wad_bin_start));
    uart_puts(" panel0=");
    uart_hex((uintptr_t)panel[0]);
    uart_puts("\r\n");
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
        "csrr t0, mtval\nsw t0, 80(sp)\n"
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
    .preinit_array : { __preinit_array_start = .; KEEP(*(.preinit_array*)) __preinit_array_end = .; } > RAM
    .init_array : { __init_array_start = .; KEEP(*(SORT(.init_array.*))) KEEP(*(.init_array)) __init_array_end = .; } > RAM
    .fini_array : { __fini_array_start = .; KEEP(*(SORT(.fini_array.*))) KEEP(*(.fini_array)) __fini_array_end = .; } > RAM
    .wad : { . = ALIGN(4); KEEP(*(.wad)) } > RAM
    .data : { . = ALIGN(4); *(.data .data.* .sdata .sdata.*) . = ALIGN(4); } > RAM
    __global_pointer$ = . + 0x800;
    _file_end = .;
    .bss (NOLOAD) : { . = ALIGN(4); *(.bss .bss.* .sbss .sbss.*) *(COMMON) . = ALIGN(4); } > RAM
    _memory_end = .;
    .stack (NOLOAD) : { . = ALIGN(16); . += 256K; __stack_top = .; } > RAM
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


def tools():
    gcc = Path(json.loads((ROOT / '.tools.local.json').read_text())['gcc'])
    return gcc, gcc.parent


def build(out, wad, defines=(), cflags=(), soft_float=False, uart=(), optimize='-O2', sources=None):
    gcc, bindir = tools()
    scratch = out / 'tmp'
    scratch.mkdir(parents=True, exist_ok=True)
    for name in ('TMP', 'TEMP', 'TMPDIR'):
        os.environ[name] = str(scratch)
    # Scripted UART characters become a C initialiser; the count is passed
    # separately so an empty script still compiles (a zero-length array is not
    # standard C).
    entries = ', '.join('{%d, %d}' % (int(ms), ord(ch)) for ms, ch in uart) or '{0, 0}'
    shim = (SHIM.replace('SIM_UART_SCRIPT', entries)
                .replace('SIM_UART_COUNT', str(len(uart))))
    (out / 'sim_shim.c').write_text(shim, encoding='utf-8')
    (out / 'sim.ld').write_text(LINKER, encoding='utf-8')
    shutil.copyfile(wad, out / WAD_BLOB)
    wad_obj = out / 'wad.o'
    subprocess.run([str(bindir / tool_executable('riscv-none-elf-objcopy')), '-I', 'binary',
                    '-O', 'elf32-littleriscv', '-B', 'riscv',
                    '--rename-section', '.data=.wad,alloc,load,readonly,data,contents',
                    WAD_BLOB, 'wad.o'], check=True, cwd=str(out))
    march, mabi = (('rv32im_zicsr_zifencei', 'ilp32') if soft_float
                   else ('rv32imaf_zicsr_zifencei', 'ilp32f'))
    libraries=newlib_link(gcc,march,mabi,out)
    if sources is None:
        sources = sorted([p for p in APP.iterdir() if p.suffix in ('.c', '.S')])
    elf = out / 'sim.elf'
    args = [str(gcc), '-march=' + march, '-mabi=' + mabi, optimize, '-Wall', '-Wextra',
            '-Wno-error', '-ffreestanding', '-fno-builtin', '-ffunction-sections',
            '-fdata-sections', '-msmall-data-limit=0', '-nostdlib', '-nostartfiles',
            '-I', str(ROOT), '-I', str(APP), '-I', str(ROOT / 'firmware/libc'),
            '-DBIOS_LIBC_HEAP_BYTES=(48u*1024u*1024u)',
            '-DDOOMGENERIC_RESX=480', '-DDOOMGENERIC_RESY=272',
            *[(d if d.startswith('-D') else f'-D{d}') for d in defines],
            *cflags,
            str(out / 'sim_shim.c'),
            str(ROOT / 'firmware/bios/payload_start.S'),
            *[str(p) for p in sources],
            *[str(p) for p in HELPERS],
            str(wad_obj),
            '-T', str(out / 'sim.ld'), '-Wl,--gc-sections',
            '-Wl,-Map,' + str(out / 'sim.map'), *libraries, '-o', str(elf)]
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL)
    return elf


def symbol(elf, name):
    _gcc, bindir = tools()
    text = subprocess.check_output([str(bindir / tool_executable('riscv-none-elf-nm')), str(elf)], text=True)
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == name:
            return int(parts[0], 16)
    raise SystemExit(f'symbol {name} not found in {elf}')


class Monitor:
    """Minimal QEMU monitor client (plain text protocol over TCP)."""

    def __init__(self, host, port, timeout=30):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.buf = b''
        time.sleep(0.5)
        self.read()

    def read(self, wait=0.4):
        self.sock.settimeout(wait)
        try:
            while True:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                self.buf += chunk
        except socket.timeout:
            pass
        out, self.buf = self.buf, b''
        return out.decode(errors='replace')

    def command(self, text, wait=1.5):
        self.sock.sendall((text + '\n').encode())
        time.sleep(wait)
        return self.read()

    def pmemsave(self, addr, size, path):
        self.command(f'pmemsave {addr} {size} "{path}"', wait=2.0)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def raw_to_png(raw, png, width=480, height=272):
    data = Path(raw).read_bytes()
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        row = data[y * width * 2:(y + 1) * width * 2]
        out = bytearray()
        for x in range(width):
            v = row[x * 2] | (row[x * 2 + 1] << 8)
            r = (v >> 11) & 0x1f
            g = (v >> 5) & 0x3f
            b = v & 0x1f
            out += bytes((r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2))
        rows += out

    def chunk(tag, payload):
        return (struct.pack('>I', len(payload)) + tag + payload
                + struct.pack('>I', zlib.crc32(tag + payload) & 0xffffffff))

    ihdr = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    Path(png).write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr)
                          + chunk(b'IDAT', zlib.compress(bytes(rows), 6))
                          + chunk(b'IEND', b''))


def run(elf, qemu, out, timeout, samples, cpu='rv32'):
    """Run the payload.  Samples only capture frames; the run ends on its own
    when the guest reaches SIM_MAX_PRESENTS (the shim's test finisher) or when
    the timeout expires."""
    uart_log = out / 'uart.log'
    if uart_log.exists():
        uart_log.unlink()
    address = symbol(elf, 'panel')
    cmd = [str(qemu), '-machine', 'virt', '-m', '256', '-display', 'none',
           '-bios', 'none', '-kernel', str(elf), '-cpu', cpu, '-no-reboot',
           '-serial', f'file:{uart_log}',
           '-monitor', 'tcp:127.0.0.1:45454,server,nowait']
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    frames = []
    start = time.monotonic()
    deadline = start + timeout
    monitor = None
    timed_out = False
    try:
        for i, delay in enumerate(samples):
            # Absolute sample times: give the guest time to boot and render.
            wait_until = min(start + delay, deadline)
            while time.monotonic() < wait_until and proc.poll() is None:
                time.sleep(0.25)
            if proc.poll() is not None:
                break
            if monitor is None:
                try:
                    monitor = Monitor('127.0.0.1', 45454)
                except OSError as exc:
                    print(f'monitor unavailable: {exc}', flush=True)
                    break
            raw = out / f'panel-{i}-{int(delay)}s.raw'
            monitor.pmemsave(address, PANEL_BYTES, str(raw).replace('\\', '/'))
            if raw.exists() and raw.stat().st_size == PANEL_BYTES:
                png = raw.with_suffix('.png')
                raw_to_png(raw, png)
                frames.append(png)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
        else:
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                timed_out = True
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if monitor is not None:
            monitor.close()
    text = uart_log.read_text(errors='replace') if uart_log.exists() else ''
    (out / 'sim.log').write_text(text, encoding='utf-8', errors='replace')
    return proc.returncode, text, frames, timed_out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir', type=Path, default=ROOT / 'build/doom-sim')
    p.add_argument('--wad', type=Path, default=ROOT / 'build/doom-assets/DOOM1.WAD')
    p.add_argument('--qemu', type=Path)
    p.add_argument('--timeout', type=float, default=300.0)
    p.add_argument('--max-presents', type=int, default=100000)
    p.add_argument('--sample', type=float, action='append', default=[],
                   help='capture the panel at this many seconds after start '
                        '(repeatable); samples never end the run')
    p.add_argument('--expect', action='append', default=[])
    p.add_argument('--uart', action='append', default=[], metavar='MS:CHAR',
                   help='make CHAR readable on the simulated UART once the game clock '
                        'reaches MS ms (repeatable); this is how the payload debug '
                        'console is driven without a board, e.g. --uart 6000:p '
                        '--uart 6200:f')
    p.add_argument('--soft-float', action='store_true')
    p.add_argument('--optimize', default='-O2',
                   help='compile the same way the board image is built (mini build doom default)')
    p.add_argument('--cpu', default='rv32')
    p.add_argument('--define', action='append', default=[])
    p.add_argument('--cflag', action='append', default=[])
    a = p.parse_args()
    uart = []
    for spec in a.uart:
        ms, _, char = spec.partition(':')
        if len(char) != 1:
            raise SystemExit(f'--uart expects MS:CHAR, got {spec!r}')
        uart.append((float(ms), char))
    uart.sort(key=lambda item: item[0])
    qemu = find_qemu(a.qemu)
    if not qemu:
        raise SystemExit('qemu-system-riscv32 not found; pass --qemu or set QEMU_RISCV32')
    wad = a.wad.resolve()
    if not wad.is_file():
        raise SystemExit(f'WAD not found: {wad}')
    samples = a.sample
    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    elf = build(out, wad, a.define,
                [f'-DSIM_MAX_PRESENTS={a.max_presents}', *a.cflag], a.soft_float, uart,
                a.optimize)
    code, text, frames, timeout_hit = run(elf, qemu, out, a.timeout, samples, a.cpu)
    print(text, flush=True)
    print(f'\nframes captured: {len(frames)}')
    for f in frames:
        print('  ', f)
    if timeout_hit:
        print('SIM TIMEOUT', flush=True)
    missing = [want for want in a.expect if want not in text]
    if missing:
        print('MISSING: ' + ', '.join(repr(m) for m in missing), flush=True)
        return 1
    return 0 if not timeout_hit else 1


if __name__ == '__main__':
    sys.exit(main())
