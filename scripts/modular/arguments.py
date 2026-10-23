"""Target argument declarations registered by the single mini parser.

This module is framework-side: it declares CLI surface only and must not import
a machine backend or gateware. Machine backends read the parsed namespace.
"""
from pathlib import Path
import sys
sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[2] / 'scripts')]
from scripts.modular.storage import ROOT

def busybox(parser):

    parser.add_argument('--rootfs-only', action='store_true', help='Require an existing build with unchanged BusyBox config')

    parser.add_argument('--board-evidence', type=Path, help='Attach a passing board linux --initrd receipt')

def cpu_gcb(parser):
    parser.add_argument('--base-generator',type=Path,required=True)
    parser.add_argument('--plugins',type=Path,required=True)
    parser.add_argument('--java',type=Path)
    parser.add_argument('--sbt-launch',type=Path)

    parser.add_argument('--single-precision-only',action='store_true',help='Skip the double-precision resource experiment')

def cpu_qualification(parser):
    parser.add_argument('--rom', type=Path, required=True,
                        help='Bootloader binary to retain realistic ROM resource usage')
    parser.add_argument('--synthesis-only', action='store_true',
                        help='Stop after synthesis; hardware configuration comes from machine.json')

def doom(parser):
    parser.add_argument('--source-dir',type=Path,default=ROOT/'firmware/apps/doom')

    display=parser.add_mutually_exclusive_group()
    display.add_argument('--indexed',dest='indexed',action='store_true',help='Use the default INDEX8 display and hardware palette')
    display.add_argument('--rgb565',dest='indexed',action='store_false',help='Use RGB565 conversion for comparisons or older BIOS versions')
    parser.set_defaults(indexed=True)
    parser.add_argument('--define',action='append',default=[])
    parser.add_argument('--cflag',action='append',default=[])
    floating=parser.add_mutually_exclusive_group()
    floating.add_argument('--soft-float',dest='soft_float',action='store_true',
                          help='Use the default rv32im/ilp32 ABI for the qualified CPU without F')
    floating.add_argument('--hard-float',dest='soft_float',action='store_false',
                          help='Use rv32imaf/ilp32f; requires a separately qualified FPU CPU')
    parser.set_defaults(soft_float=True)
    parser.add_argument('--optimize',default='-O2',
                   help='payload optimization level; -O2 is the default here because DOOM is compute-bound '
                        '(the generic -Os blit loop cost 69%% of the frame time on hardware)')
    parser.add_argument('--timedemo',help='Run this WAD demo with one game tic per rendered frame')
    parser.add_argument('--benchmark-frames',type=int,default=0,help='Report a fixed window after tic 16 and return to BIOS (requires --timedemo)')
    parser.add_argument('--benchmark-checksum',action='store_true',help='After timing, report final source/palette/display CRCs for deterministic comparisons')

def gwbasic(parser):

    parser.add_argument('--soft-float', action='store_true',
                   help='build for rv32im/ilp32 instead of the default rv32imaf/ilp32f')

def linux(parser):

    parser.add_argument('--board-evidence', type=Path, help='Attach a passing board linux operation receipt')

def matrix(parser):

    parser.add_argument('--configuration-only',action='store_true',help='CPU x SD profiles plus audio clock combinations')

def nyancat(parser):    pass

def opensbi(parser):

    parser.add_argument('--source',type=Path,default=ROOT/'build/vendor/opensbi')

    parser.add_argument('--payload-path',type=Path,default=None,help='FW_PAYLOAD image (defaults to the S-mode probe)')
    parser.add_argument('--osb-name',default='OPENSBI.OSB',help='Output OSB1 file name in --output-dir')

def payload(parser):
    parser.add_argument('--source',type=Path,default=None,help='application C source; machine-specific default when omitted')
    parser.add_argument('--source-dir',type=Path,help='compile every .c/.S in this directory instead of --source')

    parser.add_argument('--include-dir',type=Path,action='append',default=[])
    parser.add_argument('--define',action='append',default=[],help='extra -D for the compile step')
    parser.add_argument('--cflag',action='append',default=[],help='extra compiler flag, appended after the defaults')
    parser.add_argument('--extra-source',type=Path,action='append',default=[])
    parser.add_argument('--helper',action='append',choices=['string'],default=[],
                   help='link a repository-provided freestanding helper library')
    parser.add_argument('--soft-float',action='store_true',help='build for rv32im/ilp32 instead of the default rv32imaf/ilp32f')
    parser.add_argument('--optimize',default='-Os',help='optimization level for the payload (default -Os; -O2 is worth several times the speed for compute-bound apps)')
    parser.add_argument('--libc',choices=('none','newlib'),default='none',help='link Newlib and the BIOS syscall runtime')

def uboot(parser):
    parser.add_argument("--source", default="$HOME/uboot-port",
                        help="WSL-native source path (default $HOME/uboot-port)")
    parser.add_argument("--obj", default="$HOME/uboot-port-obj",
                        help="WSL-native build output path (default $HOME/uboot-port-obj)")



def release(parser):
    """Release creation options; the 20K operation module parses the namespace."""
    parser.add_argument('--hardware', dest='build',
                        help='Explicit catalog template (configuration/input CPU RTL), e.g. current')
    parser.add_argument('--version', help='System bundle version to release; must be pinned in RELEASES.yaml and tagged system/v<version>')
    parser.add_argument('--snapshot', action='store_true', help='Archive a code commit without creating a new code release; retain version plus Git hash')
    parser.add_argument('--source', dest='app', type=Path, help='Explicit application for legacy templates without application_source')
    parser.add_argument('--board-check', action='store_true', help='Program the board, run firmware checks and 60 s soak')
    parser.add_argument('--baseline', help='Explicit baseline ID/reference; enables final all/cache measurements')
    parser.add_argument('--baseline-results', type=Path, action='append', default=[])
    parser.add_argument('--allow-legacy-results', action='store_true')
