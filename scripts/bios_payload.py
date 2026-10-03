"""Build a custom bare-metal payload using the resident BIOS ecall interface."""
import argparse,json,subprocess,os
from pathlib import Path
from bios_image import pack
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=ROOT/'firmware/examples/bios_demo.c')
    p.add_argument('--output-dir',type=Path,default=ROOT/'build/bios-payload')
    p.add_argument('--include-dir',type=Path,action='append',default=[])
    p.add_argument('--extra-source',type=Path,action='append',default=[])
    a=p.parse_args();out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    scratch=out/'tmp';scratch.mkdir(exist_ok=True)
    for name in ('TMP','TEMP','TMPDIR'):os.environ[name]=str(scratch)
    gcc=Path(json.loads((ROOT/'.tools.local.json').read_text())['gcc']);bin=gcc.parent
    bios=ROOT/'firmware/bios';elf=out/'payload.elf';raw=out/'payload.bin'
    args=[str(gcc),'-march=rv32im_zicsr_zifencei','-mabi=ilp32','-Os','-Wall','-Wextra','-Werror','-ffreestanding','-fno-builtin','-ffunction-sections','-fdata-sections','-msmall-data-limit=0','-nostdlib','-nostartfiles',
        *[flag for directory in a.include_dir for flag in ('-I',str(directory.resolve()))],
        str(bios/'payload_start.S'),str(a.source.resolve()),*[str(p.resolve()) for p in a.extra_source],
        '-T',str(bios/'payload.ld'),'-Wl,--gc-sections','-Wl,-Map,'+str(out/'payload.map'),'-lgcc','-o',str(elf)]
    subprocess.run(args,check=True)
    subprocess.run([str(bin/'riscv-none-elf-objcopy.exe'),'-O','binary',str(elf),str(raw)],check=True)
    text=subprocess.check_output([str(bin/'riscv-none-elf-nm.exe'),str(elf)],text=True)
    symbols={parts[2]:int(parts[0],16) for line in text.splitlines() if len(parts:=line.split())==3}
    image=pack(raw.read_bytes(),symbols['_memory_end']-0x41000000,symbols['_payload_start'])
    (out/'BOOT.RPB').write_bytes(image)
    print('BIOS payload:',len(image),'bytes; entry',hex(symbols['_payload_start']))
if __name__=='__main__':main()
