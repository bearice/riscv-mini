"""Build the single base SoC, its boot ROM and DDR application. No automatic download."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.boot_image import LOAD, FLASH_OFFSET, abi_tag, pack_image

def checked(command,log=None):
    command=[str(part) for part in command]
    print('RUN',' '.join(command),flush=True)
    if log:
        log.parent.mkdir(parents=True,exist_ok=True)
        with log.open('w',encoding='utf-8') as stream:
            result=subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
        if result.returncode:
            print(log.read_text(encoding='utf-8',errors='replace')[-8000:])
            raise SystemExit(f'Command failed; full log: {log}')
    else: subprocess.run(command,cwd=ROOT,check=True)

def generate(output,binary=None,synthesize=False):
    from gateware.soc import MiniSoC
    from litex.soc.integration.builder import Builder
    data=None
    if binary:
        raw=binary.read_bytes();raw+=bytes((-len(raw))%4)
        data=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)]
    soc=MiniSoC(rom_data=data)
    builder=Builder(soc,output_dir=str(output),compile_software=False,compile_gateware=synthesize,
                    csr_json=str(output/'csr.json'),csr_csv=str(output/'csr.csv'))
    builder.build(run=synthesize,build_name='riscv_mini')

def generate_csr(csr,include):
    lines=['#pragma once','#include <stdint.h>']
    for name,reg in csr['csr_registers'].items():
        count,addr=reg['size'],reg['addr']
        if count not in (1,2):raise ValueError(f'Unsupported CSR width: {name}')
        typ='uint64_t' if count==2 else 'uint32_t'
        reads=' | '.join(f'(({typ})*(volatile uint32_t *)0x{addr+4*i:08x}u << {32*(count-i-1)})' for i in range(count))
        writes=' '.join(f'*(volatile uint32_t *)0x{addr+4*i:08x}u = (uint32_t)(value >> {32*(count-i-1)});' for i in range(count))
        lines += [f'#define CSR_{name.upper()}_ADDR 0x{addr:08x}u',f'#define CSR_{name.upper()}_SIZE {count}',
                  f'static inline {typ} {name}_read(void) {{ return {reads}; }}',
                  f'static inline void {name}_write({typ} value) {{ {writes} }}']
    (include/'generated').mkdir(parents=True,exist_ok=True)
    (include/'hw').mkdir(exist_ok=True)
    (include/'generated/csr.h').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    (include/'hw/common.h').write_text('#pragma once\n#include <stdint.h>\n',encoding='utf-8')

def pnr_report(output):
    class ReportText(HTMLParser):
        def __init__(self):super().__init__();self.parts=[]
        def handle_data(self,text):
            if text.strip():self.parts.append(text.strip())
    timing=ReportText();timing.feed((output/'gateware/impl/pnr/project_tr_content.html').read_text(errors='replace'))
    text=' '.join(timing.parts);counts={}
    for kind in ('Setup','Hold'):
        match=re.search(f'Numbers of {kind} Violated Endpoints (\\d+)',text)
        if not match:raise RuntimeError(f'Cannot verify {kind} timing')
        counts[kind.lower()]=int(match[1])
    resources={};text=(output/'gateware/impl/pnr/project.rpt.txt').read_text(errors='replace')
    for kind in ('Logic','Register','BSRAM','rPLL'):
        match=re.search(rf'^\s*{kind}\s*\|\s*(\d+)/(\d+)',text,re.MULTILINE)
        if match:resources[kind]={'used':int(match[1]),'available':int(match[2])}
    return counts,resources

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--synthesize',action='store_true')
    p.add_argument('--output-dir',type=Path,default=ROOT/'build/base')
    p.add_argument('--generate-only',action='store_true',help=argparse.SUPPRESS)
    p.add_argument('--rom',type=Path,help=argparse.SUPPRESS)
    a=p.parse_args();output=a.output_dir.resolve()
    if a.generate_only:generate(output,a.rom,a.synthesize);return
    output.mkdir(parents=True,exist_ok=True)
    tools=json.loads((ROOT/'.tools.local.json').read_text());gcc=Path(tools['gcc']);toolbin=gcc.parent
    os.environ['PATH']=os.pathsep.join([str(toolbin),str(Path(tools['gowin']).parent),os.environ['PATH']])
    os.environ['PYTHONUTF8']='1'
    checked([sys.executable,__file__,'--generate-only','--output-dir',output],output/'generate.log')
    csr=json.loads((output/'csr.json').read_text())
    for name,base,size in [('rom',0,8192),('sram',0x10000000,8192),('main_ram',0x40000000,128*1024*1024)]:
        if csr['memories'][name]['base']!=base or csr['memories'][name]['size']!=size:raise ValueError(f'Unexpected {name} layout')
    firmware=output/'firmware';firmware.mkdir(exist_ok=True)
    include=firmware/'include';generate_csr(csr,include)
    abi=abi_tag(csr);(firmware/'image_abi.h').write_text(f'#define MINI_IMAGE_ABI 0x{abi:08x}u\n',encoding='utf-8')
    loader=ROOT/'firmware/bootloader';drivers=ROOT/'firmware/drivers';vendor=ROOT/'firmware/vendor/fatfs'
    flags=['-march=rv32im_zicsr_zifencei','-mabi=ilp32','-Os','-Wall','-Wextra','-Werror','-ffreestanding',
        '-fno-builtin','-ffunction-sections','-fdata-sections','-nostdlib','-nostartfiles','-msmall-data-limit=0',
        '-I',firmware,'-I',include,'-I',output/'software/include','-I',drivers,'-I',loader,'-I',vendor]
    common=[ROOT/'firmware/boot/start.S',drivers/'uart.c',drivers/'time.c',drivers/'flash.c']
    for name,main,sources,linker in [
        ('boot',loader/'main.c',[ROOT/'firmware/boot/ddr.c'],loader/'boot.ld'),
        ('app',ROOT/'firmware/app/main.c',[*[drivers/n for n in ('spi.c','lcd.c','sd.c','filesystem.c','string.c','video.c')],vendor/'ff.c',vendor/'ffunicode.c'],loader/'app.ld')]:
        elf=firmware/f'{name}.elf'
        # Whole-program optimization keeps the ROM loader compact; app stays
        # separately linked and carries SD/display drivers only in DDR.
        compact=['-flto'] if name=='boot' else []
        checked([gcc,*flags,*compact,*common,main,*sources,'-T',linker,'-Wl,--gc-sections',f'-Wl,-Map,{firmware/f"{name}.map"}','-lgcc','-o',elf])
        checked([toolbin/'riscv-none-elf-objcopy.exe','-O','binary',elf,firmware/f'{name}.bin'])
        checked([toolbin/'riscv-none-elf-size.exe',elf])
    binary=firmware/'boot.bin';size=binary.stat().st_size
    if size>8192:raise RuntimeError('Boot ROM overflow')
    image=pack_image((firmware/'app.bin').read_bytes(),abi);(firmware/'app.img').write_bytes(image)
    command=[sys.executable,__file__,'--generate-only','--output-dir',output,'--rom',binary]
    if a.synthesize:command+=['--synthesize']
    checked(command,output/('synthesis.log' if a.synthesize else 'generate-rom.log'))
    # ROM content is validated independently of the compiler exit code.
    raw=binary.read_bytes();expected=[f'{int.from_bytes(raw[i:i+4],"little"):08x}' for i in range(0,len(raw),4)]
    actual=(output/'gateware/riscv_mini_rom.init').read_text().lower().split()
    if actual!=expected:raise RuntimeError('ROM content mismatch')
    report={'profile':'base: Flash/UART bootloader + DDR application','rom_size_bytes':8192,'sram_size_bytes':8192,
        'firmware_bytes':size,'firmware_sha256':hashlib.sha256(raw).hexdigest(),'isa':'rv32im_zicsr_zifencei','abi':'ilp32',
        'clock_hz':60000000,'ddr_clock_hz':120000000,'rtl':str(output/'gateware/riscv_mini.v'),
        'synthesis_requested':a.synthesize,'board_test':'not performed',
        'boot_image':{'abi_tag':abi,'flash_offset':FLASH_OFFSET,'load_address':LOAD,'entry':LOAD,
                      'image_bytes':len(image),'sha256':hashlib.sha256(image).hexdigest()}}
    if a.synthesize:
        fs=output/'gateware/riscv_mini.fs'
        if not fs.is_file() or not fs.stat().st_size:raise RuntimeError('Missing bitstream')
        counts,resources=pnr_report(output)
        report.update(bitstream=str(fs),bitstream_sha256=hashlib.sha256(fs.read_bytes()).hexdigest(),
                      timing_violated_endpoints=counts,resources=resources)
    (output/'validation.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    if a.synthesize and any(counts.values()):raise RuntimeError(f'Timing violations: {counts}')
    print(f'Base build verified: boot={size} bytes, app image={len(image)} bytes, ABI={abi:08x}',flush=True)

if __name__=='__main__':main()
