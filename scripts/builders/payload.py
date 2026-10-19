"""Build a custom bare-metal payload using the resident BIOS ecall interface."""
import json,subprocess,os
from pathlib import Path
from bios_image import pack
from payload_libc import newlib_link
from riscv_toolchain import executable as tool_executable
ROOT=Path(__file__).resolve().parents[2]
# A payload links with -nostdlib, so the freestanding helpers the compiler still
# emits calls to (memcpy, memset, strlen, ...) have to be linked in explicitly.
# The repository already provides them for the firmware; a payload that needs
# them asks for them by name instead of duplicating the code.
HELPERS={'string':ROOT/'firmware/drivers/string.c'}


def build(a, run=None):
    out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    if a.libc=='newlib' and a.helper:
        raise ValueError('Newlib supplies standard helpers; omit --helper when using --libc newlib')
    scratch=out/'tmp';scratch.mkdir(exist_ok=True)
    for name in ('TMP','TEMP','TMPDIR'):os.environ[name]=str(scratch)
    from scripts.modular.environment import discover
    environment=discover(['target.cc','target.objcopy','target.nm'])
    if run:
        from scripts.modular.storage import write
        write(run.path/'environment.json',environment)
    gcc=Path(environment['bindings']['target.cc'][0]);bin=gcc.parent
    bios=ROOT/'firmware/bios';elf=out/'payload.elf';raw=out/'payload.bin'
    march,mabi=('rv32im_zicsr_zifencei','ilp32') if a.soft_float else ('rv32imaf_zicsr_zifencei','ilp32f')
    if a.source_dir:
        directory=a.source_dir.resolve()
        sources=sorted([x for x in directory.iterdir() if x.suffix in ('.c','.S')])
        if not sources:raise SystemExit(f'no sources in {directory}')
    else:
        sources=[(a.source or ROOT/'firmware/examples/bios_demo.c').resolve()]
    runtime=[ROOT/'firmware/libc/runtime.c'] if a.libc=='newlib' else []
    libraries=newlib_link(gcc,march,mabi,out) if runtime else ['-lgcc']
    args=[str(gcc),'-march='+march,'-mabi='+mabi,a.optimize,'-Wall','-Wextra','-Werror','-ffreestanding','-fno-builtin','-ffunction-sections','-fdata-sections','-msmall-data-limit=0','-nostdlib','-nostartfiles',
        '-I',str(ROOT/'firmware/libc'),
        *[f'-D{d}' for d in a.define],*a.cflag,
        *[flag for directory in a.include_dir for flag in ('-I',str(directory.resolve()))],
        str(bios/'payload_start.S'),*[str(x.resolve()) for x in sources+runtime],*[str(x.resolve()) for x in a.extra_source],*[str(HELPERS[name]) for name in a.helper],
        '-T',str(bios/'payload.ld'),'-Wl,--gc-sections','-Wl,-Map,'+str(out/'payload.map'),*libraries,'-o',str(elf)]
    subprocess.run(args,check=True)
    subprocess.run([str(bin/tool_executable('riscv-none-elf-objcopy')),'-O','binary',str(elf),str(raw)],check=True)
    text=subprocess.check_output([str(bin/tool_executable('riscv-none-elf-nm')),str(elf)],text=True)
    symbols={parts[2]:int(parts[0],16) for line in text.splitlines() if len(parts:=line.split())==3}
    image=pack(raw.read_bytes(),symbols['_memory_end']-0x01000000,symbols['_payload_start'])
    (out/'BOOT.RPB').write_bytes(image)
    print('BIOS payload:',len(image),'bytes; entry',hex(symbols['_payload_start']))
