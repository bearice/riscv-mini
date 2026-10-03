"""Build pinned single-hart OpenSBI and its S-mode probe using WSL LLVM."""
import argparse,json,shlex,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.build import generate_csr
from scripts.bios_image import pack
REV="6ad246a1494807ee91e36ffcf60e4c30bb309d45"
def linux(p):
    p=Path(p).resolve().as_posix();return '/mnt/'+p[0].lower()+p[2:]
COMMANDS=[]
def wsl(args,log=None):
    command=shlex.join([str(a) for a in args])
    if log:command+=' > '+shlex.quote(linux(log))+' 2>&1'
    COMMANDS.append(command)
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--soc-dir',type=Path,required=True)
    parser.add_argument('--source',type=Path,default=ROOT/'build/opensbi-research')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'build/opensbi/firmware')
    parser.add_argument('--pack-only',action='store_true')
    a=parser.parse_args();src=a.source.resolve();out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    if not src.exists():
        subprocess.run(['git','clone','-c','core.autocrlf=false','https://github.com/riscv-software-src/opensbi',str(src)],check=True)
        subprocess.run(['git','-C',str(src),'checkout',REV],check=True)
    if subprocess.check_output(['git','-C',str(src),'rev-parse','HEAD'],text=True).strip()!=REV:
        raise ValueError('OpenSBI source revision differs from pinned version')
    if a.pack_only:
        pack_output(out);return
    # Python entrypoint shebangs must retain LF when the checkout is on Windows.
    for script in (src/'scripts').rglob('*'):
        if script.suffix not in ('.py','.sh'):continue
        script.write_bytes(script.read_bytes().replace(b'\r\n',b'\n'))
    for config in src.rglob('*.carray'):
        config.write_bytes(config.read_bytes().replace(b'\r\n',b'\n'))
    patch=ROOT/'firmware/opensbi/patches/no-mdt.patch'
    # Idempotent: refuse a changed upstream source rather than guessing a patch.
    if subprocess.run(['git','-C',str(src),'apply','--reverse','--check',str(patch)],capture_output=True).returncode:
        subprocess.run(['git','-C',str(src),'apply',str(patch)],check=True)
    csr=json.loads((a.soc_dir/'csr.json').read_text())
    if 'cpu_timer_time' not in csr['csr_registers']:raise ValueError('SoC needs CPU machine timer')
    generate_csr(csr,out/'include')
    header=(out/'include/generated/csr.h').read_text()
    for k,v in csr['constants'].items():
        if isinstance(v,int):header+='\n#define '+k.upper()+' '+str(v)+'u'
    plat=src/'platform/riscv_mini';shutil.copytree(ROOT/'firmware/opensbi/platform',plat,dirs_exist_ok=True)
    (plat/'mini_csr.h').write_text(header.replace('#include <stdint.h>', '#include <sbi/sbi_types.h>'))
    (out/'mini_csr.h').write_text(header)
    dts=out/'riscv_mini.dts';dtb=out/'riscv_mini.dtb'
    uart=csr['csr_bases']['uart']
    dts.write_text('/dts-v1/;\n/ { #address-cells=<1>; #size-cells=<1>; compatible="riscv-mini,tangprimer20k"; model="riscv-mini"; '
        'cpus { #address-cells=<1>; #size-cells=<0>; timebase-frequency=<60000000>; '
        'cpu@0 { device_type="cpu"; reg=<0>; compatible="riscv"; riscv,isa="rv32imaf_zicsr_zifencei"; mmu-type="riscv,sv32"; cpu_intc: interrupt-controller { #interrupt-cells=<1>; interrupt-controller; compatible="riscv,cpu-intc"; }; }; }; '
        'irq: interrupt-controller { compatible="riscv-mini,vexriscv-supervisor-irq"; #interrupt-cells=<1>; interrupt-controller; interrupts-extended=<&cpu_intc 9>; riscv-mini,mask-csr=<0x9c0>; riscv-mini,pending-csr=<0xdc0>; }; '
        'memory@40000000 { device_type="memory"; reg=<0x40000000 0x08000000>; }; '
        'reserved-memory { #address-cells=<1>; #size-cells=<1>; ranges; '
        'opensbi@41000000 { reg=<0x41000000 0x100000>; no-map; }; }; '
        'soc { #address-cells=<1>; #size-cells=<1>; compatible="simple-bus"; ranges; '
        f'serial@{uart:x} {{ compatible="litex,uart"; reg=<0x{uart:x} 0x800>; interrupt-parent=<&irq>; interrupts=<0>; }}; }}; }};\n')
    wsl(['dtc','-I','dts','-O','dtb','-o',linux(dtb),linux(dts)])
    probe=ROOT/'firmware/opensbi/probe';elf=out/'probe.elf';raw=out/'probe.bin'
    wsl(['clang','--target=riscv32-unknown-elf','-march=rv32ima_zicsr_zifencei','-mabi=ilp32','-Os','-Wall','-Wextra','-Werror',
         '-ffreestanding','-nostdlib','-msmall-data-limit=0','-fuse-ld=lld','-I'+linux(out),
         linux(probe/'start.S'),linux(probe/'probe.c'),linux(probe/'mmu.c'),'-Wl,-T,'+linux(probe/'probe.ld'),'-o',linux(elf)],out/'probe-build.log')
    wsl(['llvm-objcopy','-O','binary',linux(elf),linux(raw)])
    wsl(['make','-C',linux(src),'-j4','LLVM=1','PLATFORM=riscv_mini','O='+linux(out/'sbi-soft'),
         'FW_PAYLOAD_PATH='+linux(raw), 'FW_FDT_PATH='+linux(dtb)],out/'opensbi-build.log')
    (out/'build.sh').write_text('set -eu\n'+'\n'.join(COMMANDS)+'\n',newline='\n')
    print('Prepared',out/'build.sh')
def pack_output(out):
    binary=out/'sbi-soft/platform/riscv_mini/firmware/fw_payload.bin'
    # Keep the ordinary RPB checksum/layout; OSB1 selects one-way BIOS entry.
    image=pack(binary.read_bytes(),0x200000,os_image=True)
    (out/'OPENSBI.OSB').write_bytes(image)
    print('OpenSBI OS image:',len(image),'bytes')
if __name__=='__main__':main()
