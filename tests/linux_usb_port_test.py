"""Exercise the actual HCD root-hub callback with synthetic PHY line samples."""
import argparse
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', help='Run the callback from an earlier Git revision')
    args = parser.parse_args()
    source = ROOT / 'firmware/linux/riscv_mini_usb.c'
    text = (subprocess.check_output(['git', 'show', args.revision + ':firmware/linux/riscv_mini_usb.c'],
                                    cwd=ROOT).decode() if args.revision else source.read_text(encoding='utf-8'))
    callback = text.split('static int mini_hub_status(', 1)[1].split('static int mini_hub_control(', 1)[0]
    output = ROOT / 'build/reports/checks/linux-usb-port'
    output.mkdir(parents=True, exist_ok=True)
    fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <limits.h>
#include <stdio.h>
#include "riscv_mini_usb_port.h"
#define READ_ONCE(x) (x)
#define spin_lock_irqsave(lock, flags) do { (void)(lock); (flags)=0; } while (0)
#define spin_unlock_irqrestore(lock, flags) do { (void)(lock); (void)(flags); } while (0)
#define USB_PORT_STAT_C_CONNECTION 1
#define STATUS 4
#define msecs_to_jiffies(x) (x)
static unsigned long jiffies;
static unsigned int line;
struct mini_usb { void *regs; int lock; unsigned int change;
    bool enabled, connected, resetting, transacting; struct mini_usb_port port; };
struct usb_hcd { struct mini_usb *priv; };
static struct mini_usb *mini_hcd(struct usb_hcd *hcd) { return hcd->priv; }
static unsigned int readl(void *reg) { (void)reg; return line; }
'''
    fixture += 'static int mini_hub_status(' + callback
    fixture += r'''
static int sample(struct usb_hcd *hcd, unsigned int value, unsigned long tick) {
    char buf[1]; line=value; jiffies=tick;
    int result=mini_hub_status(hcd,buf);
    assert(buf[0]==(result?2:0)); return result;
}
int main(void) {
    struct mini_usb port={.regs=(void *)0x1000}; struct usb_hcd hcd={&port};
    assert(!sample(&hcd,2,0)); /* Direct low-speed idle K cannot attach. */
    assert(sample(&hcd,1,1)); port.change=0; port.enabled=true;
    assert(!sample(&hcd,2,2)); /* Packet K must not detach an attached FS device. */
    assert(!sample(&hcd,0,3)); assert(!sample(&hcd,0,22));
    assert(!sample(&hcd,1,23)); assert(port.enabled); /* Short EOP/SE0 recovers. */
    port.resetting=true;
    assert(!sample(&hcd,0,100)); assert(!sample(&hcd,0,140));
    port.resetting=false; port.transacting=true;
    assert(!sample(&hcd,0,200)); assert(!sample(&hcd,0,240));
    port.transacting=false;
    assert(!sample(&hcd,0,300)); assert(!sample(&hcd,0,319));
    assert(sample(&hcd,0,320)); assert(!port.enabled); port.change=0;
    assert(!sample(&hcd,2,321)); assert(sample(&hcd,1,322)); port.change=0;
    assert(!sample(&hcd,0,ULONG_MAX-10)); assert(!sample(&hcd,0,5));
    assert(sample(&hcd,0,10)); /* Wrap-safe elapsed time. */
    puts("PASS Linux root hub: packet K/EOP, reset, transfer, unplug, reattach, tick wrap");
    return 0;
}
'''
    cfile = output / 'callback.c'
    cfile.write_text(fixture, encoding='utf-8')
    cc = Path('C:/msys64/ucrt64/bin/gcc.exe')
    env = {**os.environ, 'PATH': str(cc.parent) + os.pathsep + os.environ['PATH'],
           'TMP': str(output), 'TEMP': str(output), 'TMPDIR': str(output)}
    executable = output / 'callback.exe'
    subprocess.run([str(cc), '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-I', str(source.parent), str(cfile), '-o', str(executable)], env=env, check=True)
    result = subprocess.run([str(executable)], env=env, capture_output=True, text=True)
    (output / ('baseline.log' if args.revision else 'fixed.log')).write_text(
        result.stdout + result.stderr, encoding='utf-8')
    print(result.stdout + result.stderr, end='')
    result.check_returncode()


if __name__ == '__main__':
    main()
