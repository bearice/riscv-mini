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
from gateware.memory_map import RAM_BASE, RAM_SIZE, c_header
from scripts.boot_image import LOAD, FLASH_OFFSET, abi_tag, pack_image
from gateware.features import Features
from gateware.config import SD_PROFILES, storage_profile, cpu_configuration, cpu_filename, pll_count, cpu_capabilities, cpu_isa
from gateware.flash_xip import FLASH_BASE, XIP_OFFSET, XIP_SIZE
from scripts.build_records import now, register, run_path, source_identity, write_json
from scripts.build_recipe import create_recipe
from scripts.versions import COMPONENTS, read_versions

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

def generate(output,binary=None,synthesize=False,sd_backend="native",features=None,hierarchical=True,usb_backend="ultra",cpu_variant="lite",cpu_verilog=None,sd_profile=None,audio_clock='dds',deep_verilog=False,place_option=2,route_option=2,l2_size=4096,rom_size=8192,boot_mode='rom',debug_mode='none'):
    from gateware.soc import MiniSoC
    from litex.soc.integration.builder import Builder
    data=None
    if binary:
        raw=binary.read_bytes();raw+=bytes((-len(raw))%4)
        data=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)]
    soc=MiniSoC(rom_data=data,sd_backend=sd_backend,features=features,usb_backend=usb_backend,cpu_variant=cpu_variant,cpu_verilog=cpu_verilog,sd_profile=sd_profile,audio_clock=audio_clock,l2_size=l2_size,rom_size=rom_size,boot_mode=boot_mode,debug_mode=debug_mode)
    soc.platform.toolchain.options.update(place_option=place_option,route_option=route_option)
    if hierarchical:
        from gateware.rtl import split_verilog
        split_verilog(soc.platform,deep=deep_verilog)
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
    # Read-only compatibility accessors share the gateware layout definition.
    from gateware.csr_layout import STATUS_LAYOUTS
    for bank, words in STATUS_LAYOUTS.items():
        for word, fields in words:
            packed = bank+'_'+word
            if packed not in csr['csr_registers']:continue
            shift = 0
            for name, width in fields:
                mask = (1 << width)-1
                lines.append(f'static inline uint32_t {bank}_{name}_read(void) {{ return ({packed}_read() >> {shift}) & 0x{mask:x}u; }}')
                shift += width
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
    # Compiler and vendor tools write scratch files; keep them inside the build
    # tree so a locked-down %TEMP% cannot break a build.
    scratch=ROOT/'build/.tmp'; scratch.mkdir(parents=True,exist_ok=True)
    for name in ('TMP','TMPDIR','TEMP'): os.environ[name]=str(scratch)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cpu-verilog',type=Path)
    p.add_argument('--cpu-rtl-dir',type=Path,default=ROOT/'build/cpu-features',help='Generated independent MMU/FPU CPU variants')
    p.add_argument('--cpu-variant',choices=('lite','full','linux'),default=None)
    p.add_argument('--usb-backend',choices=('ohci','ultra'),default='ultra')
    p.add_argument('--sd-backend',choices=('native','spi'),default=None,help='Legacy alias; prefer --sd-profile')
    p.add_argument('--sd-profile',choices=SD_PROFILES,default=None)
    p.add_argument('--audio-clock',choices=('dds','sys','legacy'),default='dds')
    p.add_argument('--profile',choices=('full','minimal'),default='full')
    p.add_argument('--flat-verilog',action='store_true',help='Compatibility output; default is hierarchical modules in separate files')
    p.add_argument('--deep-verilog',action='store_true',help='Diagnostic full internal hierarchy; default separates SoC blocks')
    for name in Features.names():
        group=p.add_mutually_exclusive_group()
        flag=name.replace('_','-')
        group.add_argument('--with-'+flag,dest=name,action='store_true',default=None)
        group.add_argument('--without-'+flag,dest=name,action='store_false')
    p.add_argument('--debug-mode',choices=('none','transport','cpu'),default='none',help='Experimental Gowin JTAGBone / CPU debug resource comparison')
    p.add_argument('--synthesize',action='store_true')
    p.add_argument('--l2-size',type=int,choices=(4096,8192),default=4096,help='Shared writeback cache bytes; >=4 KiB required for boot RAM')
    p.add_argument('--rom-size',type=int,choices=(4096,8192),default=8192,help='Boot ROM address window in bytes; linker rejects overflow')
    p.add_argument('--boot-mode',choices=('rom','xip'),default='rom',help='Execute boot code from ROM or mapped Flash')
    p.add_argument('--without-compressed',action='store_true',help='Reject CPU RTL with the C extension')
    p.add_argument('--place-option',type=int,choices=range(5),default=2)
    p.add_argument('--route-option',type=int,choices=range(3),default=2)
    p.add_argument('--output-dir',type=Path,help='Explicit legacy/matrix path; default is a unique build/runs identity')
    p.add_argument('--purpose',default='candidate',help='Purpose included in managed run name and catalog')
    p.add_argument('--app',type=Path,default=ROOT/'firmware/bios/main.c',help='DDR firmware source (default: resident BIOS); bootloader is unchanged')
    p.add_argument('--generate-only',action='store_true',help=argparse.SUPPRESS)
    p.add_argument('--rom',type=Path,help=argparse.SUPPRESS)
    a=p.parse_args();a.audio_clock='dds' if a.audio_clock=='sys' else a.audio_clock
    if a.flat_verilog and a.deep_verilog:p.error('Use flat or deep Verilog, not both')
    overrides={name:getattr(a,name) for name in Features.names()}
    try:
        features=Features.resolve(a.profile,overrides)
        a.sd_profile,features=storage_profile(a.sd_profile,a.sd_backend,features,overrides)
        a.sd_backend='native' if a.sd_profile in ('lite','full') else 'spi' if a.sd_profile=='spi' else 'none'
        if a.cpu_verilog is None and (features.mmu or features.fpu):
            a.cpu_verilog=a.cpu_rtl_dir/cpu_filename(features.mmu,features.fpu)
            if not a.cpu_verilog.is_file():raise ValueError(f'Missing CPU RTL {a.cpu_verilog}; run scripts/cpu_generate.py first')
        features,a.cpu_variant,cpu_dcache=cpu_configuration(features,overrides,a.cpu_verilog,a.cpu_variant)
        expected_plls=pll_count(features,a.usb_backend,a.audio_clock)
    except ValueError as error:p.error(str(error))
    capabilities=cpu_capabilities(a.cpu_verilog) if a.cpu_verilog else {'mmu':False,'fpu':False,'dcache':False,'compressed':False,'bitmanip':[]}
    isa=cpu_isa(features,capabilities)
    if a.without_compressed and capabilities.get('compressed'):p.error('Selected CPU RTL enables C')
    if a.boot_mode=='xip' and not features.flash:p.error('XIP requires Flash')
    if a.generate_only and a.output_dir is None:p.error('--generate-only requires --output-dir')
    if a.output_dir is None:
        output,source=run_path(a.profile,isa,a.rom_size if a.boot_mode=='rom' else 0,a.l2_size,a.purpose)
    else:
        output=a.output_dir.resolve()
        source=None if a.generate_only else source_identity()
    config_args=['--cpu-variant',a.cpu_variant]+(['--cpu-verilog',str(a.cpu_verilog.resolve())] if a.cpu_verilog else [])+['--usb-backend',a.usb_backend,'--sd-profile',a.sd_profile,'--audio-clock',a.audio_clock]+features.arguments()+(['--flat-verilog'] if a.flat_verilog else [])+(['--deep-verilog'] if a.deep_verilog else [])
    config_args+=['--place-option',str(a.place_option),'--route-option',str(a.route_option)]
    config_args+=['--l2-size',str(a.l2_size)]
    config_args+=['--rom-size',str(a.rom_size)]
    config_args+=['--debug-mode',a.debug_mode]
    config_args+=['--boot-mode',a.boot_mode]+(['--without-compressed'] if a.without_compressed else [])
    requirements={'microphone_demo.c':('mic','video'),
                  'microphone_stereo_demo.c':('mic','mic_stereo','video'),
                  'usb_input_demo.c':('usb','video'),'ethernet_demo.c':('eth',),
                  'audio_demo.c':('audio',),'sd_demo.c':('sd','filesystem')}
    if a.app.resolve().parent==ROOT/'firmware/examples':
        missing=[name for name in requirements.get(a.app.name,()) if not getattr(features,name)]
        if missing:p.error(f'{a.app.name} requires enabled modules: {", ".join(missing)}')
    if a.generate_only:generate(output,a.rom,a.synthesize,a.sd_backend,features,not a.flat_verilog,a.usb_backend,a.cpu_variant,a.cpu_verilog,a.sd_profile,a.audio_clock,a.deep_verilog,a.place_option,a.route_option,a.l2_size,a.rom_size,a.boot_mode,a.debug_mode);return
    output.mkdir(parents=True,exist_ok=True)
    source.update(kind='captured',created_at=now(),arguments=sys.argv[1:],
                  cpu_rtl_sha256=hashlib.sha256(a.cpu_verilog.read_bytes()).hexdigest() if a.cpu_verilog else None)
    recipe_args=['--profile',a.profile,'--app',str(a.app.resolve()),'--purpose',a.purpose,*config_args]+(['--synthesize'] if a.synthesize else [])
    source['recipe']=str(create_recipe(output,source,recipe_args,a.cpu_verilog))
    write_json(output/'build-info.json',dict(status='building',source=source,purpose=a.purpose))
    print('Build directory:',output,flush=True)
    tools=json.loads((ROOT/'.tools.local.json').read_text());gcc=Path(tools['gcc']);toolbin=gcc.parent
    os.environ['PATH']=os.pathsep.join([str(toolbin),str(Path(tools['gowin']).parent),os.environ['PATH']])
    os.environ['PYTHONUTF8']='1'
    checked([sys.executable,__file__,'--generate-only','--output-dir',output,*config_args],output/'generate.log')
    csr=json.loads((output/'csr.json').read_text())
    if csr['constants']['config_sd_native']!=int(a.sd_backend=='native'):raise ValueError('SD hardware/backend mismatch')
    memories=[('main_ram',RAM_BASE,RAM_SIZE)]
    memories += [('rom',FLASH_BASE,a.rom_size)] if a.boot_mode=='rom' else [('spiflash',FLASH_BASE,0x400000)]
    if a.boot_mode=='xip' and 'rom' in csr['memories']:raise ValueError('XIP must have no ROM')
    for name,base,size in memories:
        if csr['memories'][name]['base']!=base or csr['memories'][name]['size']!=size:raise ValueError(f'Unexpected {name} layout')
    if 'sram' in csr['memories']:raise ValueError('Integrated SRAM must be absent')
    firmware=output/'firmware';firmware.mkdir(exist_ok=True)
    include=firmware/'include';generate_csr(csr,include)
    (include/'memory_layout.h').write_text(c_header(),encoding='utf-8')
    capabilities=cpu_capabilities(a.cpu_verilog) if a.cpu_verilog else {'mmu':False,'fpu':False,'dcache':False,'compressed':False,'bitmanip':[]}
    isa=cpu_isa(features,capabilities)
    component_versions=read_versions()
    # The ROM is the bootloader; the DDR application is a separate component. Each banner must
    # carry its own component's version, never the other's.
    app_component='bios' if a.app.resolve()==ROOT/'firmware/bios/main.c' else None
    app_version=component_versions[app_component] if app_component else '0.0.0-dev'
    boot_version=component_versions['bootloader']
    version_defines=''.join(f'#define MINI_VERSION_{name.upper()} "{component_versions[name]}"\n' for name in COMPONENTS)
    rtl_digest=hashlib.sha256()
    for rel in ('gateware/riscv_mini.v','gateware/rtl-manifest.json'):
        path=output/rel
        rtl_digest.update(path.read_bytes() if path.is_file() else b'<missing>')
    if a.cpu_verilog:rtl_digest.update(a.cpu_verilog.read_bytes())
    (include/'features.h').write_text(features.header()+f'#define MINI_CPU_ISA "{isa}"\n#define MINI_CPU_COMPRESSED {int(capabilities["compressed"])}\n#define MINI_CPU_BITMANIP {int(bool(capabilities["bitmanip"]))}\n#define MINI_SD_PROFILE "{a.sd_profile}"\n'
        +version_defines
        +f'#define MINI_BUILD_VERSION "{boot_version}"\n#define MINI_APP_VERSION "{app_version}"\n#define MINI_BUILD_COMMIT "{source["commit"][:7]}"\n'
        +f'#define MINI_BUILD_DIRTY {int(bool(source["dirty"]))}\n#define MINI_BUILD_CONFIG "{source["inputs_sha256"][:8]}"\n'
        +f'#define MINI_BUILD_RTL "{rtl_digest.hexdigest()[:8]}"\n'
        '#if MINI_BUILD_DIRTY\n#define MINI_BUILD_SUFFIX ".dirty"\n#else\n#define MINI_BUILD_SUFFIX ""\n#endif\n'
        '#define MINI_BUILD_ID MINI_BUILD_VERSION "+" MINI_BUILD_COMMIT "." MINI_BUILD_CONFIG MINI_BUILD_SUFFIX\n'
        '#define MINI_APP_ID MINI_APP_VERSION "+" MINI_BUILD_COMMIT "." MINI_BUILD_CONFIG MINI_BUILD_SUFFIX\n'
        '#define MINI_BUILD_RTL_ID "rtl" MINI_BUILD_RTL\n',encoding='utf-8')
    abi=abi_tag(csr);(firmware/'image_abi.h').write_text(f'#define MINI_IMAGE_ABI 0x{abi:08x}u\n',encoding='utf-8')
    loader=ROOT/'firmware/bootloader';drivers=ROOT/'firmware/drivers';vendor=ROOT/'firmware/vendor/fatfs';hal=ROOT/'firmware/hal'
    usb=ROOT/'firmware/vendor/tinyusb/src'
    flags=['-DMINI_CPU_DCACHE='+str(int(cpu_dcache)), '-DMINI_SD_LITE='+str(int(a.sd_profile=='lite')), '-march='+isa,'-mabi=ilp32','-Os','-Wall','-Wextra','-Werror','-ffreestanding',
        '-fno-builtin','-ffunction-sections','-fdata-sections','-nostdlib','-nostartfiles','-msmall-data-limit=0',
        '-I',firmware,'-I',include,'-I',output/'software/include','-I',drivers,'-I',loader,'-I',vendor,'-I',hal/'include','-I',usb,
        '-include',include/'features.h']
    common=[ROOT/'firmware/boot/start.S',drivers/'uart.c',drivers/'time.c']
    app_sources=[drivers/'string.c',*[hal/'src'/n for n in ('board.c','irq.c','trap.S','devices.c','disabled.c')]]
    if features.spi_lcd or (features.sd and a.sd_backend=='spi'):app_sources.append(drivers/'spi.c')
    if features.spi_lcd:app_sources.append(drivers/'lcd.c')
    if features.sd:app_sources.append(drivers/('sd_native.c' if a.sd_backend=='native' else 'sd.c'))
    if features.filesystem:app_sources += [drivers/'filesystem.c',vendor/'ff.c',vendor/'ffunicode.c']
    if features.video:app_sources.append(drivers/'video.c')
    for feature,driver_source in [('audio','audio.c'),('mic','microphone.c'),('eth','ethernet.c'),('usb','usb.c')]:
        if getattr(features,feature):app_sources.append(hal/'src'/driver_source)
    if features.usb:
        app_sources += [usb/n for n in ('tusb.c','common/tusb_fifo.c','host/usbh.c','host/hub.c','class/hid/hid_host.c')]
        app_sources.append(hal/'src/hcd_ultra.c' if a.usb_backend=='ultra' else usb/'portable/ohci/ohci.c')
    firmware_sizes={}
    for name,main,sources,linker in [
        ('boot',loader/'main.c',[loader/'ddr.c'],loader/('boot.ld' if a.boot_mode=='rom' else 'xip.ld')),
        ('app',a.app.resolve(),app_sources,loader/'app.ld')]:
        elf=firmware/f'{name}.elf'
        flash_source=loader/('flash.c' if a.boot_mode=='rom' else 'flash_xip.c') if name=='boot' and features.flash else drivers/('flash.c' if features.flash else 'flash_disabled.c')
        # Whole-program optimization keeps the ROM loader compact; app stays
        # separately linked and carries SD/display drivers only in DDR.
        compact=['-flto','-fstack-usage','-DMINI_BOOTLOADER=1'] if name=='boot' else ['-DMINI_BOOTLOADER=0']
        if name=='boot' and a.boot_mode=='rom':compact += [f'-Wl,--defsym,BOOT_ROM_SIZE={a.rom_size}']
        if name=='boot' and a.boot_mode=='xip':compact += [f'-Wl,--defsym,BOOT_XIP_ORIGIN={FLASH_BASE+XIP_OFFSET}',f'-Wl,--defsym,BOOT_XIP_SIZE={XIP_SIZE}']
        if name=='app' and main==ROOT/'firmware/examples/monitor.c':sources=[*sources,ROOT/'firmware/diagnostics/tests.c']
        if name=='app' and main==ROOT/'firmware/bios/main.c':
            sources=[*sources,ROOT/'firmware/diagnostics/tests.c',*[ROOT/'firmware/bios'/n for n in ('console.c','settings.c','boot.c','network.c','benchmark.c','enter.S')]]
            compact+=['-DMINI_BIOS=1']
        checked([gcc,*flags,*compact,*common,flash_source,main,*sources,'-T',linker,'-Wl,--gc-sections',f'-Wl,-Map,{firmware/f"{name}.map"}','-lgcc','-o',elf])
        checked([toolbin/'riscv-none-elf-objcopy.exe','-O','binary',elf,firmware/f'{name}.bin'])
        sizes=subprocess.check_output([str(toolbin/'riscv-none-elf-size.exe'),str(elf)],text=True)
        print(sizes,flush=True)
        values=sizes.splitlines()[1].split()
        firmware_sizes[name]={kind:int(value) for kind,value in zip(('text','data','bss'),values[:3])}
        firmware_sizes[name]['binary_bytes']=(firmware/f'{name}.bin').stat().st_size
        if name=='boot':
            # ROM is linked before the app, so its digest can be stamped into the BIOS build.
            rom_sha=hashlib.sha256((firmware/'boot.bin').read_bytes()).hexdigest()
            with (include/'features.h').open('a',encoding='utf-8') as stream:
                stream.write(f'#define MINI_BUILD_ROM "{rom_sha[:8]}"\n#define MINI_BUILD_ROM_ID "rom" MINI_BUILD_ROM\n')
    binary=firmware/'boot.bin';size=binary.stat().st_size
    if size>(a.rom_size if a.boot_mode=='rom' else XIP_SIZE):raise RuntimeError('Boot code overflow')
    if a.boot_mode=='xip':(firmware/'xip.bin').write_bytes(binary.read_bytes())
    image=pack_image((firmware/'app.bin').read_bytes(),abi);(firmware/'app.img').write_bytes(image)
    command=[sys.executable,__file__,'--generate-only','--output-dir',output,'--rom',binary,*config_args]
    if a.synthesize:command+=['--synthesize']
    checked(command,output/('synthesis.log' if a.synthesize else 'generate-rom.log'))
    # ROM content is validated independently of the compiler exit code.
    raw=binary.read_bytes();expected=[f'{int.from_bytes(raw[i:i+4],"little"):08x}' for i in range(0,len(raw),4)]
    # Switching flat/hierarchical output can leave an unused old init file.
    # Validate the ROM referenced by this build, rather than all directory files.
    rom_name='riscv_mini_rom.init' if a.flat_verilog else 'riscv_mini__rom_rom.init'
    rom_files=[output/'gateware'/rom_name]
    rom_files=[path for path in rom_files if path.is_file()]
    if a.boot_mode=='rom':
        if len(rom_files)!=1:raise RuntimeError(f'Expected one boot ROM initialization file: {rom_files}')
        actual=rom_files[0].read_text().lower().split()
        if actual!=expected:raise RuntimeError('ROM content mismatch')
    elif rom_files:raise RuntimeError('Unexpected ROM init in XIP build')
    report={'profile':a.profile,'boot_mode':a.boot_mode,'boot_sources':['flash','uart'] if features.flash else ['uart'],'rom_size_bytes':a.rom_size if a.boot_mode=='rom' else 0,'sram_size_bytes':0,
        'ddr_initialization':'software','boot_ram_address':0x007ff000,'boot_ram_size_bytes':4096,'boot_ram_backend':'pinned-l2',
        'firmware_bytes':size,'firmware_sha256':hashlib.sha256(raw).hexdigest(),'firmware_sizes':firmware_sizes,
        'cpu_variant':a.cpu_variant,'cpu_verilog':str(a.cpu_verilog.resolve()) if a.cpu_verilog else None,'isa':isa,'abi':'ilp32',
         'cpu_capabilities':capabilities,'l2_size_bytes':a.l2_size,
        'memory_system':'shared-writeback',
        'dma_backend':'native' if features.sd and a.sd_profile=='lite' else 'wishbone',
        'native_dma_clients':['sd'] if features.sd and a.sd_profile=='lite' else [],
        'l2_policy':'write-back',
        'sd_profile':a.sd_profile,'audio_clock':a.audio_clock,
        'expected_plls':expected_plls,'audio_reference_hz':60000000,
        'audio_sample_rate':(46875 if a.audio_clock=='legacy' else 48000) if features.audio else 0,
        'mic_sample_rate':(46875 if a.audio_clock=='legacy' else 48000) if features.mic else 0,
        'clock_hz':60000000,'ddr_clock_hz':120000000,'usb_backend':a.usb_backend,'usb_phy_clock_hz':(60000000 if a.usb_backend=='ultra' else 48000000) if features.usb else 0,'ulpi_clock_hz':60000000 if features.usb else 0,'rtl':str(output/'gateware/riscv_mini.v'),
        'features':features.as_dict(),'verilog_mode':'flat' if a.flat_verilog else 'hierarchical',
        'netlist_hierarchy':None if a.flat_verilog else 0,
        'rtl_hierarchy':'flat' if a.flat_verilog else 'deep' if a.deep_verilog else 'blocks',
        'place_option':a.place_option,'route_option':a.route_option,
        'debug_mode':a.debug_mode,'synthesis_requested':a.synthesize,'board_test':'not performed','sd_backend':a.sd_backend,
        'application_source':str(a.app.resolve()),
        'build_id':f'{app_component or "app"}-{app_version}+{source["commit"][:7]}.{source["inputs_sha256"][:8]}' + ('.dirty' if source['dirty'] else ''),
        'component_versions':component_versions,'app_component':app_component,
        'rtl_sha256':rtl_digest.hexdigest(),'config_sha256':source['inputs_sha256'],
        'boot_image':{'abi_tag':abi,'flash_offset':FLASH_OFFSET,'load_address':LOAD,'entry':LOAD,
                      'image_bytes':len(image),'sha256':hashlib.sha256(image).hexdigest()}}
    if a.boot_mode=='xip':
        report['xip']={'base':FLASH_BASE,'reset_address':FLASH_BASE+XIP_OFFSET,'flash_offset':XIP_OFFSET,
                       'reserved_bytes':XIP_SIZE,'spi_hz':10000000,'binary':str(firmware/'xip.bin'),
                       'sha256':hashlib.sha256(raw).hexdigest(),'application_execution':'DDR'}
    if not a.flat_verilog:
        report['rtl_manifest']=str(output/'gateware/rtl-manifest.json')
        report['rtl_modules']=len(json.loads((output/'gateware/rtl-manifest.json').read_text())['modules'])
    if a.synthesize:
        fs=output/'gateware/riscv_mini.fs'
        if not fs.is_file() or not fs.stat().st_size:raise RuntimeError('Missing bitstream')
        counts,resources=pnr_report(output)
        report.update(bitstream=str(fs),bitstream_sha256=hashlib.sha256(fs.read_bytes()).hexdigest(),
                      timing_violated_endpoints=counts,resources=resources)
    if source_identity()['inputs_sha256'] != source['inputs_sha256']:
        raise RuntimeError('Build source inputs changed while building; rerun with stable inputs')
    (output/'validation.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    record=register(output,a.purpose,source)
    write_json(output/'build-info.json',dict(status=record['status'],source=source,id=record['id'],purpose=a.purpose))
    if a.synthesize and any(counts.values()):raise RuntimeError(f'Timing violations: {counts}')
    print(f'Base build verified: boot={size} bytes, app image={len(image)} bytes, ABI={abi:08x}',flush=True)

if __name__=='__main__':main()
