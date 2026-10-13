"""Build the DOOM payload image (BOOT.RPB) for the riscv-mini BIOS.

firmware/apps/doom holds a doomgeneric checkout plus two local files:
  shim.c                 DOOM-specific CRC/progress/debug hooks
  firmware/libc/         common BIOS syscall adapter for statically linked Newlib
  dg_bios.c               video/input/time platform layer
The panel is 480x272 and DOOM renders 320x200, so the payload defines
DOOMGENERIC_RESX/RESY to the panel size (auto scaling picks factor 1 and the
image is drawn straight into the BIOS framebuffer).
"""
import argparse,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-dir',type=Path,default=ROOT/'firmware/apps/doom')
    p.add_argument('--output-dir',type=Path,default=ROOT/'build/doom-payload')
    display=p.add_mutually_exclusive_group()
    display.add_argument('--indexed',dest='indexed',action='store_true',help='Use the default INDEX8 display and hardware palette')
    display.add_argument('--rgb565',dest='indexed',action='store_false',help='Use RGB565 conversion for comparisons or older BIOS versions')
    p.set_defaults(indexed=True)
    p.add_argument('--define',action='append',default=[])
    p.add_argument('--cflag',action='append',default=[])
    floating=p.add_mutually_exclusive_group()
    floating.add_argument('--soft-float',dest='soft_float',action='store_true',
                          help='Use the default rv32im/ilp32 ABI for the qualified CPU without F')
    floating.add_argument('--hard-float',dest='soft_float',action='store_false',
                          help='Use rv32imaf/ilp32f; requires a separately qualified FPU CPU')
    p.set_defaults(soft_float=True)
    p.add_argument('--optimize',default='-O2',
                   help='payload optimization level; -O2 is the default here because DOOM is compute-bound '
                        '(the generic -Os blit loop cost 69%% of the frame time on hardware)')
    p.add_argument('--timedemo',help='Run this WAD demo with one game tic per rendered frame')
    p.add_argument('--benchmark-frames',type=int,default=0,help='Report a fixed window after tic 16 and return to BIOS (requires --timedemo)')
    p.add_argument('--benchmark-checksum',action='store_true',help='After timing, report final source/palette/display CRCs for deterministic comparisons')
    a=p.parse_args()
    if a.indexed:a.define.append("DOOM_INDEX8=1")
    if a.benchmark_frames<0 or (a.benchmark_frames and not a.timedemo):
        p.error('--benchmark-frames must be non-negative and requires --timedemo')
    if a.benchmark_checksum:
        if not a.benchmark_frames:p.error('--benchmark-checksum requires --benchmark-frames')
        a.define.append('DOOM_BENCH_CHECKSUM=1')
    if a.timedemo:
        if not a.timedemo.isalnum() or len(a.timedemo)>8:p.error('--timedemo requires a WAD lump name of up to eight letters/digits')
        a.define += [f'DOOM_TIMEDEMO="{a.timedemo}"',f'DOOM_BENCH_FRAMES={a.benchmark_frames}']
    app=a.source_dir.resolve()
    cmd=[sys.executable,str(ROOT/'scripts/bios_payload.py'),
         '--source-dir',str(app),
         '--output-dir',str(a.output_dir.resolve()),
         '--libc','newlib',
         '--define','BIOS_LIBC_HEAP_BYTES=(48u*1024u*1024u)',
         # '-O2' looks like a flag to argparse, so it has to travel attached.
         '--optimize='+a.optimize,
         '--define','DOOMGENERIC_RESX=480','--define','DOOMGENERIC_RESY=272',
         *[f'--define={d}' for d in a.define],
         # The vendored Chocolate Doom sources predate modern -Wall/-Wextra:
         # keep the warnings visible, but do not fail the build on them.
         '--cflag=-Wno-error',*[f'--cflag={f}' for f in a.cflag]]
    if a.soft_float:
        cmd.append('--soft-float')
    print('$',' '.join(cmd),flush=True)
    subprocess.run(cmd,check=True)
if __name__=='__main__':main()
