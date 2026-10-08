"""Build a custom bare-metal payload using the resident BIOS ecall interface."""
import argparse,json,subprocess,os
from pathlib import Path
from bios_image import pack
ROOT=Path(__file__).resolve().parents[1]
# A payload links with -nostdlib, so the freestanding helpers the compiler still
# emits calls to (memcpy, memset, strlen, ...) have to be linked in explicitly.
# The repository already provides them for the firmware; a payload that needs
# them asks for them by name instead of duplicating the code.
HELPERS={'string':ROOT/'firmware/drivers/string.c'}
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=ROOT/'firmware/examples/bios_demo.c')
    p.add_argument('--source-dir',type=Path,help='compile every .c/.S in this directory instead of --source')
    p.add_argument('--output-dir',type=Path,default=ROOT/'build/bios-payload')
    p.add_argument('--include-dir',type=Path,action='append',default=[])
    p.add_argument('--extra-source',type=Path,action='append',default=[])
    p.add_argument('--helper',action='append',choices=sorted(HELPERS),default=[],
                   help='link a repository-provided freestanding helper library')
    p.add_argument('--soft-float',action='store_true',help='build for rv32im/ilp32 instead of the default rv32imaf/ilp32f')
    a=p.parse_args();out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    scratch=out/'tmp';scratch.mkdir(exist_ok=True)
    for name in ('TMP','TEMP','TMPDIR'):os.environ[name]=str(scratch)
    gcc=Path(json.loads((ROOT/'.tools.local.json').read_text())['gcc']);bin=gcc.parent
    bios=ROOT/'firmware/bios';elf=out/'payload.elf';raw=out/'payload.bin'
    march,mabi=('rv32im_zicsr_zifencei','ilp32') if a.soft_float else ('rv32imaf_zicsr_zifencei','ilp32f')
    if a.source_dir:
        directory=a.source_dir.resolve()
        sources=sorted([x for x in directory.iterdir() if x.suffix in ('.c','.S')])
        if not sources:raise SystemExit(f'no sources in {directory}')
    else:
        sources=[a.source.resolve()]
    args=[str(gcc),'-march='+march,'-mabi='+mabi,'-Os','-Wall','-Wextra','-Werror','-ffreestanding','-fno-builtin','-ffunction-sections','-fdata-sections','-msmall-data-limit=0','-nostdlib','-nostartfiles',
        *[flag for directory in a.include_dir for flag in ('-I',str(directory.resolve()))],
        str(bios/'payload_start.S'),*[str(x.resolve()) for x in sources],*[str(x.resolve()) for x in a.extra_source],*[str(HELPERS[name]) for name in a.helper],
        '-T',str(bios/'payload.ld'),'-Wl,--gc-sections','-Wl,-Map,'+str(out/'payload.map'),'-lgcc','-o',str(elf)]
    subprocess.run(args,check=True)
    subprocess.run([str(bin/'riscv-none-elf-objcopy.exe'),'-O','binary',str(elf),str(raw)],check=True)
    text=subprocess.check_output([str(bin/'riscv-none-elf-nm.exe'),str(elf)],text=True)
    symbols={parts[2]:int(parts[0],16) for line in text.splitlines() if len(parts:=line.split())==3}
    image=pack(raw.read_bytes(),symbols['_memory_end']-0x01000000,symbols['_payload_start'])
    (out/'BOOT.RPB').write_bytes(image)
    print('BIOS payload:',len(image),'bytes; entry',hex(symbols['_payload_start']))
if __name__=='__main__':main()
