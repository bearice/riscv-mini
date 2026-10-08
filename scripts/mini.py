"""riscv-mini task entry point. Start here; commands own sequencing and checks."""
import argparse
import sys
import subprocess
from pathlib import Path


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='group',required=True)
    board=sub.add_parser('board',help='Observe, recover, run, update or verify the board')
    operations=board.add_subparsers(dest='operation',required=True)
    for name,description in [
        ('status','Passively observe UART; silence is unknown, no reset'),
        ('recover','SRAM load selected gateware, enter loader and query Flash header'),
        ('repair-xip','Restore selected XIP boot region, then SRAM load and exact readback; writes Flash'),
        ('run','SRAM gateware + UART application to DDR; no Flash writes'),
        ('update','Persist complete system, exact Flash readback, then Flash boot'),
        ('verify','Check firmware, boot protocol, exact Flash contents or external cold boot')]:
        op=operations.add_parser(name,help=description,description=description)
        op.add_argument('--port',default='COM4')
        if name=='status':
            op.add_argument('--seconds',type=float,default=2)
            continue
        op.add_argument('--build',default='current',help='Catalog ID/reference or artifact directory; default qualified current')
        op.add_argument('--location',default='107569')
        op.add_argument('--dry-run',action='store_true',help='Validate selected artifacts and show intent; no hardware access')
        if name in ('run','update','verify'):
            op.add_argument('--image',type=Path,help='Compatible app.img override; default selected build image')
        if name=='verify':
            op.add_argument('--suite',choices=('firmware','boot','flash','spi','cold'),default='firmware')
            op.add_argument('--soak-seconds',type=int,default=0)
            op.add_argument('--timeout',type=int,default=300,help='External cold-boot observation timeout')
            op.add_argument('--mic',action='store_true',help='Firmware suite: test connected microphones')
    builds=sub.add_parser('builds',help='Find a build without choosing directories by timestamp')
    commands=builds.add_subparsers(dest='operation',required=True)
    commands.add_parser('list')
    show=commands.add_parser('show');show.add_argument('name',nargs='?',default='current')
    build=sub.add_parser('build',help='Build current sources using the qualified configuration, or bootstrap minimal')
    template=build.add_mutually_exclusive_group()
    template.add_argument('--from',dest='template',default='current',help='Configuration template ID/reference/directory')
    template.add_argument('--minimal',action='store_true',help='Bootstrap without existing full-system CPU RTL')
    build.add_argument('--app',type=Path,help='Application source override')
    build.add_argument('--purpose',default='development')
    build.add_argument('--synthesize',action='store_true',help='Also synthesize and place/route; never programs the board')
    build.add_argument('--dry-run',action='store_true')
    sub.add_parser('doctor',help='Check host dependencies and configured toolchain; no board access')
    sub.add_parser('guide',help='Print the task guide and location of specialized operations')
    return p


def main(argv=None):
    p=parser()
    argv=sys.argv[1:] if argv is None else argv
    if not argv:
        p.print_help();return
    args=p.parse_args(argv)
    if args.group=='board':
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
    elif args.group=='builds':
        from build_records import get_record, records
        import json
        if args.operation=='list':
            for record in reversed(records()):print(record['status'],record['id'])
        else:print(json.dumps(get_record(args.name),indent=2,ensure_ascii=False))
    elif args.group=='doctor':
        from doctor import main as doctor
        doctor()
    elif args.group=='build':
        from mini_ops.build import perform
        try:perform(args)
        except (ValueError,OSError,KeyError,subprocess.SubprocessError) as error:p.exit(1,str(error)+'\n')
    else:
        print((Path(__file__).parent/'README.md').read_text(encoding='utf-8'))


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    main()
