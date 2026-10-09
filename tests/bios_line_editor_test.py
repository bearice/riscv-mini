"""Compile the actual BIOS editor loop with host I/O stubs; no board required.

The command dispatcher and startup use hardware, so extract just the loop from
main.c rather than maintain a second implementation of the editor in the test.
Run with --cc <native C compiler> if cc is not on PATH.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = r'''
#include <stdio.h>
#include <string.h>
#include <setjmp.h>
static const unsigned char *input;
static jmp_buf done;
static char executed[128],output[4096];
static unsigned output_len,checks,failures;
static void bios_poll(void) {}
static void tests_poll(void) {}
static int bios_getc(void) {if(!*input)longjmp(done,1);return *input++;}
static void bios_putc(char c) {if(output_len<sizeof(output)-1)output[output_len++]=c;}
static void bios_puts(const char *s) {while(*s)bios_putc(*s++);}
static void hal_reboot(void) {}
static void command(char *s) {strcpy(executed,s);}
static void edit(void) {
'''
SUFFIX = r'''
}
static void probe(const char *label,const char *keys,const char *want,const char *echo) {
 input=(const unsigned char *)keys;executed[0]=0;output_len=0;
 if(!setjmp(done))edit();
 output[output_len]=0;
 ++checks;
 if(strcmp(executed,want) || (echo && strcmp(output,echo))) {
  ++failures;printf("FAIL %s: got='%s' expected='%s'\n",label,executed,want);
 }
}
int main(void) {
 probe("Left then overwrite","abc\033[DX\r","abX",0);
 probe("SS3 Left","abc\033ODX\r","abX",0);
 probe("Right from Home","abc\033[H\033[CX\r","aXc",0);
 probe("Up/Down do not edit","abc\033[A\033[BX\r","abcX",0);
 probe("Backspace in middle","abcde\033[H\033[C\033[C\010\r","acde",0);
 probe("Backspace at start","abc\033[H\010\r","abc",0);
 probe("Delete at caret","abc\033[H\033[C\033[3~\r","ac",0);
 probe("Ctrl-W preserves tail","one two tail\033[H\033[C\033[C\033[C\033[C\033[C\033[C\033[C\027\r","one  tail",0);
 probe("Ctrl-W trailing spaces","one two  tail\033[H\033[C\033[C\033[C\033[C\033[C\033[C\033[C\033[C\033[C\027\r","one tail",0);
 probe("Ctrl-W at start","abc\001\027\r","abc",0);
 probe("Ctrl-W at end","one two\027\r","one ",0);
 probe("Insert preserves tail","abc\001\033[2~X\r","Xabc",0);
 probe("Tail append echo","abc\r","abc","> abc\r\n> ");
 probe("Tail backspace echo","abc\010\r","ab","> abc\b \b\r\n> ");
 printf("%u checks, %u failures\n",checks,failures);
 return failures!=0;
}
'''

HID_PREFIX = r'''
#define main mapping_test_main
#include "USB_TEST"
#undef main
#include <setjmp.h>
#include <stdlib.h>
static jmp_buf done;
static char executed[128];
void bios_poll(void) {}
void tests_poll(void) {}
void hal_reboot(void) {abort();}
static void command(char *s) {strcpy(executed,s);}
static int editor_getc(void) {int c=bios_getc();if(c<0)longjmp(done,1);return c;}
static void edit(void) {
'''
HID_SUFFIX = r'''
}
static void text_keys(const char *s) {while(*s){press(*s==' '?44:(unsigned)(*s-'a'+4),0);++s;}}
static void run_editor(void) {executed[0]=0;repeat_held=0;if(!setjmp(done))edit();}
int main(void) {
 bios_console_init(0);
 text_keys("one two");press(26,0x01);press(40,0);run_editor();
 check("USB Ctrl-W reaches editor at tail",!strcmp(executed,"one "));
 text_keys("one two tail");press(74,0);
 for(unsigned i=0;i<7;++i)press(79,0);
 press(26,0x10);press(40,0);run_editor();
 check("USB Right Ctrl-W preserves editor tail",!strcmp(executed,"one  tail"));
 printf("HID-to-editor: %u checks, %u failures\n",checks,failures);
 return failures!=0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cc', default=shutil.which('cc') or 'C:/msys64/ucrt64/bin/gcc.exe')
    args = parser.parse_args()
    source = (ROOT / 'firmware/bios/main.c').read_text(encoding='utf-8')
    start = source.index('    char line[128];')
    loop = source[start:source.rfind('}')]
    env = {**os.environ, 'PATH': str(Path(args.cc).resolve().parent) + os.pathsep + os.environ['PATH']}
    temporary_root = ROOT / 'build' / 'tests'
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='bios-editor-', dir=temporary_root) as temp:
        path = Path(temp)
        env.update(TEMP=str(path), TMP=str(path), TMPDIR=str(path))
        test = path / 'editor.c'
        test.write_text(PREFIX + loop + SUFFIX, encoding='utf-8')
        exe = path / ('editor.exe' if os.name == 'nt' else 'editor')
        subprocess.run([args.cc, '-Wall', '-Wextra', '-Werror', str(test), '-o', str(exe)], env=env, check=True)
        subprocess.run([str(exe)], env=env, check=True)
        # Exercise the actual console.c -> input queue -> editor chain as well.
        generated = path / 'headers/generated'
        generated.mkdir(parents=True, exist_ok=True)
        for name in ('csr.h','soc.h'):
            (generated/name).write_text('/* CSR bodies are stubbed by usb_key_map_test.c. */\n')
        hid = path / 'hid-editor.c'
        hid.write_text(HID_PREFIX.replace('USB_TEST', (ROOT/'tests/usb_key_map_test.c').as_posix())
                       + loop.replace('bios_getc()', 'editor_getc()') + HID_SUFFIX, encoding='utf-8')
        subprocess.run([args.cc, '-Wall','-Wextra','-Werror','-DMINI_BOOTLOADER=0','-DMINI_FEATURE_USB=1',
                        '-I',str(generated.parent),'-I',str(ROOT/'firmware/hal/include'),
                        '-I',str(ROOT/'firmware/bios'),str(hid),str(ROOT/'firmware/bios/vt.c'),
                        '-o',str(exe)],env=env,check=True)
        subprocess.run([str(exe)],env=env,check=True)


if __name__ == '__main__':
    main()
