"""Build the DOOM payload image (BOOT.RPB) for the riscv-mini BIOS.

firmware/apps/doom holds a doomgeneric checkout plus two local files:
  shim.c                 DOOM-specific CRC/progress/debug hooks
  firmware/libc/         common BIOS syscall adapter for statically linked Newlib
  dg_bios.c               video/input/time platform layer
The panel is 480x272 and DOOM renders 320x200, so the payload defines
DOOMGENERIC_RESX/RESY to the panel size (auto scaling picks factor 1 and the
image is drawn straight into the BIOS framebuffer).
"""
import subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]


def build(a, run=None):
    if a.indexed:a.define.append("DOOM_INDEX8=1")
    if a.benchmark_frames<0 or (a.benchmark_frames and not a.timedemo):
        raise ValueError('--benchmark-frames must be non-negative and requires --timedemo')
    if a.benchmark_checksum:
        if not a.benchmark_frames:raise ValueError('--benchmark-checksum requires --benchmark-frames')
        a.define.append('DOOM_BENCH_CHECKSUM=1')
    if a.timedemo:
        if not a.timedemo.isalnum() or len(a.timedemo)>8:raise ValueError('--timedemo requires a WAD lump name of up to eight letters/digits')
        a.define += [f'DOOM_TIMEDEMO="{a.timedemo}"',f'DOOM_BENCH_FRAMES={a.benchmark_frames}']
    app=a.source_dir.resolve()
    from types import SimpleNamespace
    from scripts.builders.payload import build as payload
    payload(SimpleNamespace(source=None, source_dir=app, output_dir=a.output_dir,
        include_dir=[], define=['BIOS_LIBC_HEAP_BYTES=(48u*1024u*1024u)',
        'DOOMGENERIC_RESX=480','DOOMGENERIC_RESY=272',*a.define],
        cflag=['-Wno-error',*a.cflag], extra_source=[], helper=[],
        soft_float=a.soft_float, optimize=a.optimize, libc='newlib'), run=run)
