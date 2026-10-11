"""Build the DOOM payload image (BOOT.RPB) for the riscv-mini BIOS.

firmware/apps/doom holds a doomgeneric checkout plus two local files:
  shim.c / shim_include/  a small libc over the BIOS ecall services
  dg_bios.c               video/input/time platform layer
The panel is 480x272 RGB565 and DOOM renders 320x200, so the payload defines
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
    p.add_argument('--define',action='append',default=[])
    p.add_argument('--cflag',action='append',default=[])
    p.add_argument('--soft-float',action='store_true',
                   help='build rv32im/ilp32 for a CPU without the F extension (the qualified gateware)')
    p.add_argument('--optimize',default='-O2',
                   help='payload optimization level; -O2 is the default here because DOOM is compute-bound '
                        '(the generic -Os blit loop cost 69%% of the frame time on hardware)')
    a=p.parse_args()
    app=a.source_dir.resolve()
    cmd=[sys.executable,str(ROOT/'scripts/bios_payload.py'),
         '--source-dir',str(app),
         '--output-dir',str(a.output_dir.resolve()),
         '--include-dir',str(app/'shim_include'),
         '--helper','string',
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
