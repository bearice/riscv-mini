"""Build a configurable SoC, boot ROM and DDR application. No automatic download."""
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
from gateware.features import Features

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

def generate(output,binary=None,synthesize=False,sd_backend="native",features=None,hierarchical=True):
    from gateware.soc import MiniSoC
    from litex.soc.integration.builder import Builder
    data=None
    if binary:
        raw=binary.read_bytes();raw+=bytes((-len(raw))%4)
        data=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)]
    soc=MiniSoC(rom_data=data,sd_backend=sd_backend,features=features)
    if hierarchical:
        from gateware.rtl import split_verilog
        split_verilog(soc.platform)
    builder=Builder(soc,output_dir=str(output),compile_software=False,compile_gateware=synthesize,
                    csr_json=str(output/'csr.json'),csr_csv=str(output/'csr.csv'),hierarchical=hierarchical)
    builder.build(run=synthesize,build_name='riscv_mini')

def generate_csr(csr,include):
    lines=['#pragma once','#include <stdint.h>']
    for name,reg in csr['csr_registers'].items():
        count,addr=reg['size'],reg['addr']
        if count>2:
            lines += [f'#define CSR_{name.upper()}_ADDR 0x{addr:08x}u',f'#define CSR_{name.upper()}_SIZE {count}',
                f'static inline uint32_t {name}_read_word(unsigned index) {{ return *(volatile uint32_t *)(0x{addr:08x}u+4*index); }}']
            continue
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
    for kind in ('Logic','Register','CLS','I/O Port','IOLOGIC','BSRAM',
                 'PRIMARY','LW','GCLK_PIN','CLKDIV','DHCEN','DLL','DQS','rPLL'):
        match=re.search(rf'^\s*{re.escape(kind)}\s*\|\s*(\d+)/(\d+)',text,re.MULTILINE)
        if not match:raise RuntimeError(f'Cannot verify {kind} resources')
        used,available=int(match[1]),int(match[2])
        if available<=0 or used>available:raise RuntimeError(f'Invalid {kind} resource usage')
        resources[kind]={'used':used,'available':available}
    return counts,resources

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sd-backend',choices=('native','spi'),default='native')
    p.add_argument('--profile',choices=('full','minimal'),default='full')
    p.add_argument('--flat-verilog',action='store_true',help='Compatibility output; default is hierarchical modules in separate files')
    for name in Features.names():
        group=p.add_mutually_exclusive_group()
        flag=name.replace('_','-')
        group.add_argument('--with-'+flag,dest=name,action='store_true',default=None)
        group.add_argument('--without-'+flag,dest=name,action='store_false')
    p.add_argument('--synthesize',action='store_true')
    p.add_argument('--output-dir',type=Path,default=ROOT/'build/base')
    p.add_argument('--app',type=Path,default=ROOT/'firmware/app/main.c',help='DDR application source; bootloader is unchanged')
    p.add_argument('--generate-only',action='store_true',help=argparse.SUPPRESS)
    p.add_argument('--rom',type=Path,help=argparse.SUPPRESS)
    a=p.parse_args();output=a.output_dir.resolve()
    try:features=Features.resolve(a.profile,{name:getattr(a,name) for name in Features.names()})
    except ValueError as error:p.error(str(error))
    config_args=features.arguments()+(['--flat-verilog'] if a.flat_verilog else [])
    requirements={'microphone_demo.c':('mic','video'),
                  'microphone_stereo_demo.c':('mic','mic_stereo','video'),
                  'usb_input_demo.c':('usb','video'),'ethernet_demo.c':('eth',),
                  'audio_demo.c':('audio',),'sd_demo.c':('sd','filesystem')}
    if a.app.resolve().parent==ROOT/'firmware/examples':
        missing=[name for name in requirements.get(a.app.name,()) if not getattr(features,name)]
        if missing:p.error(f'{a.app.name} requires enabled modules: {", ".join(missing)}')
    if a.generate_only:generate(output,a.rom,a.synthesize,a.sd_backend,features,not a.flat_verilog);return
    output.mkdir(parents=True,exist_ok=True)
    tools=json.loads((ROOT/'.tools.local.json').read_text());gcc=Path(tools['gcc']);toolbin=gcc.parent
    os.environ['PATH']=os.pathsep.join([str(toolbin),str(Path(tools['gowin']).parent),os.environ['PATH']])
    os.environ['PYTHONUTF8']='1'
    checked([sys.executable,__file__,'--generate-only','--output-dir',output,'--sd-backend',a.sd_backend,*config_args],output/'generate.log')
    csr=json.loads((output/'csr.json').read_text())
    if csr['constants']['config_sd_native']!=int(a.sd_backend=='native'):raise ValueError('SD hardware/backend mismatch')
    for name,base,size in [('rom',0,8192),('sram',0x10000000,8192),('main_ram',0x40000000,128*1024*1024)]:
        if csr['memories'][name]['base']!=base or csr['memories'][name]['size']!=size:raise ValueError(f'Unexpected {name} layout')
    firmware=output/'firmware';firmware.mkdir(exist_ok=True)
    include=firmware/'include';generate_csr(csr,include)
    (include/'features.h').write_text(features.header(),encoding='utf-8')
    abi=abi_tag(csr);(firmware/'image_abi.h').write_text(f'#define MINI_IMAGE_ABI 0x{abi:08x}u\n',encoding='utf-8')
    loader=ROOT/'firmware/bootloader';drivers=ROOT/'firmware/drivers';vendor=ROOT/'firmware/vendor/fatfs';hal=ROOT/'firmware/hal'
    usb=ROOT/'firmware/vendor/tinyusb/src'
    flags=['-march=rv32im_zicsr_zifencei','-mabi=ilp32','-Os','-Wall','-Wextra','-Werror','-ffreestanding',
        '-fno-builtin','-ffunction-sections','-fdata-sections','-nostdlib','-nostartfiles','-msmall-data-limit=0',
        '-I',firmware,'-I',include,'-I',output/'software/include','-I',drivers,'-I',loader,'-I',vendor,'-I',hal/'include','-I',usb,
        '-include',include/'features.h']
    common=[ROOT/'firmware/boot/start.S',drivers/'uart.c',drivers/'time.c',
            drivers/('flash.c' if features.flash else 'flash_disabled.c')]
    app_sources=[drivers/'string.c',*[hal/'src'/n for n in ('board.c','irq.c','trap.S','devices.c','disabled.c')]]
    if features.spi_lcd or (features.sd and a.sd_backend=='spi'):app_sources.append(drivers/'spi.c')
    if features.spi_lcd:app_sources.append(drivers/'lcd.c')
    if features.sd:app_sources.append(drivers/('sd_native.c' if a.sd_backend=='native' else 'sd.c'))
    if features.filesystem:app_sources += [drivers/'filesystem.c',vendor/'ff.c',vendor/'ffunicode.c']
    if features.video:app_sources.append(drivers/'video.c')
    for feature,source in [('audio','audio.c'),('mic','microphone.c'),('eth','ethernet.c'),('usb','usb.c')]:
        if getattr(features,feature):app_sources.append(hal/'src'/source)
    if features.usb:app_sources += [usb/n for n in ('tusb.c','common/tusb_fifo.c','host/usbh.c','class/hid/hid_host.c','portable/ohci/ohci.c')]
    firmware_sizes={}
    for name,main,sources,linker in [
        ('boot',loader/'main.c',[ROOT/'firmware/boot/ddr.c'],loader/'boot.ld'),
        ('app',a.app.resolve(),app_sources,loader/'app.ld')]:
        elf=firmware/f'{name}.elf'
        # Whole-program optimization keeps the ROM loader compact; app stays
        # separately linked and carries SD/display drivers only in DDR.
        compact=['-flto'] if name=='boot' else []
        if name=='app' and main==ROOT/'firmware/app/main.c':sources=[*sources,ROOT/'firmware/app/tests.c']
        checked([gcc,*flags,*compact,*common,main,*sources,'-T',linker,'-Wl,--gc-sections',f'-Wl,-Map,{firmware/f"{name}.map"}','-lgcc','-o',elf])
        checked([toolbin/'riscv-none-elf-objcopy.exe','-O','binary',elf,firmware/f'{name}.bin'])
        sizes=subprocess.check_output([str(toolbin/'riscv-none-elf-size.exe'),str(elf)],text=True)
        print(sizes,flush=True)
        values=sizes.splitlines()[1].split()
        firmware_sizes[name]={kind:int(value) for kind,value in zip(('text','data','bss'),values[:3])}
        firmware_sizes[name]['binary_bytes']=(firmware/f'{name}.bin').stat().st_size
    binary=firmware/'boot.bin';size=binary.stat().st_size
    if size>8192:raise RuntimeError('Boot ROM overflow')
    image=pack_image((firmware/'app.bin').read_bytes(),abi);(firmware/'app.img').write_bytes(image)
    command=[sys.executable,__file__,'--generate-only','--output-dir',output,'--rom',binary,'--sd-backend',a.sd_backend,*config_args]
    if a.synthesize:command+=['--synthesize']
    checked(command,output/('synthesis.log' if a.synthesize else 'generate-rom.log'))
    # ROM content is validated independently of the compiler exit code.
    raw=binary.read_bytes();expected=[f'{int.from_bytes(raw[i:i+4],"little"):08x}' for i in range(0,len(raw),4)]
    # Switching flat/hierarchical output can leave an unused old init file.
    # Validate the ROM referenced by this build, rather than all directory files.
    rom_name='riscv_mini_rom.init' if a.flat_verilog else 'riscv_mini__rom_rom.init'
    rom_files=[output/'gateware'/rom_name]
    rom_files=[path for path in rom_files if path.is_file()]
    if len(rom_files)!=1:raise RuntimeError(f'Expected one boot ROM initialization file: {rom_files}')
    actual=rom_files[0].read_text().lower().split()
    if actual!=expected:raise RuntimeError('ROM content mismatch')
    report={'profile':a.profile,'boot_sources':['flash','uart'] if features.flash else ['uart'],'rom_size_bytes':8192,'sram_size_bytes':8192,
        'firmware_bytes':size,'firmware_sha256':hashlib.sha256(raw).hexdigest(),'firmware_sizes':firmware_sizes,
        'isa':'rv32im_zicsr_zifencei','abi':'ilp32',
        'clock_hz':60000000,'ddr_clock_hz':120000000,'usb_phy_clock_hz':48000000 if features.usb else 0,'ulpi_clock_hz':60000000 if features.usb else 0,'rtl':str(output/'gateware/riscv_mini.v'),
        'features':features.as_dict(),'verilog_mode':'flat' if a.flat_verilog else 'hierarchical',
        'synthesis_requested':a.synthesize,'board_test':'not performed','sd_backend':a.sd_backend,
        'boot_image':{'abi_tag':abi,'flash_offset':FLASH_OFFSET,'load_address':LOAD,'entry':LOAD,
                      'image_bytes':len(image),'sha256':hashlib.sha256(image).hexdigest()}}
    if not a.flat_verilog:
        report['rtl_manifest']=str(output/'gateware/rtl-manifest.json')
        report['rtl_modules']=len(json.loads((output/'gateware/rtl-manifest.json').read_text())['modules'])
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
