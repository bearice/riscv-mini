"""riscv-mini task entry point. Start here; commands own sequencing and checks."""
import argparse
import sys
import subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"scripts"))


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    from scripts.modular.cli import add_commands
    add_commands(sub)
    board=sub.add_parser('board',help='Observe, recover, run, update or verify the board')
    operations=board.add_subparsers(dest='operation',required=True)
    for name,description in [
        ('status','Passively observe UART; silence is unknown, no reset'),
        ('recover','SRAM load selected gateware, enter loader and query Flash header'),
        ('repair-xip','Restore selected XIP boot region, then SRAM load and exact readback; writes Flash'),
        ('run','SRAM gateware + UART application to DDR; no Flash writes'),
        ('update','Program complete Flash image directly, without SPI Verify, then Flash boot'),
        ('verify','Check firmware, boot protocol, exact Flash contents or external cold boot'),
        ('linux','Boot a Linux Image via BIOS/OpenSBI/U-Boot TFTP and verify userspace')]:
        op=operations.add_parser(name,help=description,description=description)
        op.add_argument('--port',default='COM4')
        if name=='status':
            op.add_argument('--seconds',type=float,default=2)
            continue
        op.add_argument('--build',default='current',help='Catalog ID/reference or artifact directory; default qualified current')
        op.add_argument('--location',default=None,help='Gowin cable location override; default scans the connected JTAG cable before programming')
        op.add_argument('--dry-run',action='store_true',help='Validate selected artifacts and show intent; no hardware access')
        if name=='linux':
            op.add_argument('--os-image',type=Path,required=True,help='Matching OpenSBI/U-Boot OSB1 image')
            op.add_argument('--kernel',type=Path,required=True,help='Uncompressed RV32 Linux Image with embedded /init')
            op.add_argument('--initrd',type=Path,help='External BusyBox initrd.cpio.gz; verifies the shell instead of embedded selftests')
            op.add_argument('--host-ip',default='169.254.25.153')
            op.add_argument('--tftp-python',default=sys.executable,help='Firewall-allowed Python to run the existing read-only TFTP server')
            op.add_argument('--timeout',type=int,default=180)
            op.add_argument('--devices',action='store_true',help='Also verify native Linux device drivers and UDP echo; SD/Flash reads only')
        if name in ('run','update','verify'):
            op.add_argument('--image',type=Path,help='Compatible app.img override; default selected build image')
        if name=='update':
            op.add_argument('--method',choices=('auto','programmer'),default='programmer',
                            help='auto: DDR software SPI (needs a booting loader). programmer (default): direct Flash op8/op31, no SRAM download, SPI Verify or readback')
        if name=='verify':
            op.add_argument('--suite',choices=('firmware','boot','flash','spi','cold'),default='firmware')
            op.add_argument('--soak-seconds',type=int,default=0)
            op.add_argument('--timeout',type=int,default=300,help='External cold-boot observation timeout')
            op.add_argument('--mic',action='store_true',help='Firmware suite: test connected microphones')
    sub.add_parser('doctor',help='Check host dependencies and configured toolchain; no board access')
    sub.add_parser('guide',help='Print the task guide and location of specialized operations')
    return p


def main(argv=None):
    p=parser()
    argv=sys.argv[1:] if argv is None else argv
    if not argv:
        p.print_help();return
    args=p.parse_args(argv)
    if args.command=='board':
        if args.operation=='status' and not 0<args.seconds<=30:p.error('--seconds must be in (0,30]')
        if args.operation=='verify':
            if not 0<=args.soak_seconds<=300:p.error('--soak-seconds must be in 0..300')
            if not 1<=args.timeout<=3600:p.error('--timeout must be in 1..3600')
            if args.image and args.suite in ('boot','spi','cold'):p.error('--image is supported by firmware/flash verification only')
            if args.mic and args.suite!='firmware':p.error('--mic requires --suite firmware')
        from mini_ops.board import perform
        try:perform(args)
        except (ValueError,RuntimeError,OSError,KeyError,TimeoutError,subprocess.SubprocessError) as error:
            p.exit(1,str(error)+'\n')
    elif args.command=='doctor':
        from doctor import main as doctor
        doctor()
    elif args.command=='guide':
        print((Path(__file__).parent/'README.md').read_text(encoding='utf-8'))
    else:
        from scripts.modular.cli import perform
        code=perform(args,p)
        if code:raise SystemExit(code)



if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    main()
