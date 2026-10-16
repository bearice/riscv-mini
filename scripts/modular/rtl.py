"""RTL elaboration, CSR headers and vendor report parsing shared by every entry point."""
import re
from html.parser import HTMLParser

def generate(output,binary=None,synthesize=False,sd_backend="native",features=None,hierarchical=True,usb_backend="ultra",cpu_variant="lite",cpu_verilog=None,sd_profile=None,audio_clock='dds',deep_verilog=False,place_option=2,route_option=2,l2_size=4096,rom_size=8192,boot_mode='rom'):
    from gateware.soc import MiniSoC
    from litex.soc.integration.builder import Builder
    data=None
    if binary:
        raw=binary.read_bytes();raw+=bytes((-len(raw))%4)
        data=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)]
    soc=MiniSoC(rom_data=data,sd_backend=sd_backend,features=features,usb_backend=usb_backend,cpu_variant=cpu_variant,cpu_verilog=cpu_verilog,sd_profile=sd_profile,audio_clock=audio_clock,l2_size=l2_size,rom_size=rom_size,boot_mode=boot_mode)
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



def worker(request_path):
    from .storage import read
    from gateware.features import Features
    request = read(request_path); v = request['validation']
    from pathlib import Path
    generate(Path(request['output']), Path(request['rom']), request['synthesize'],
             sd_backend=v['sd_backend'], features=Features(**v['features']),
             hierarchical=v['verilog_mode'] != 'flat', usb_backend=v['usb_backend'],
             cpu_variant=v['cpu_variant'], cpu_verilog=Path(request['cpu']) if request['cpu'] else None,
             sd_profile=v['sd_profile'], audio_clock=v['audio_clock'],
             deep_verilog=v['rtl_hierarchy'] == 'deep', place_option=v['place_option'],
             route_option=v['route_option'], l2_size=v['l2_size_bytes'],
             rom_size=v['rom_size_bytes'] or 8192, boot_mode=v['boot_mode'])
    output=Path(request['output'])
    name='riscv_mini_rom.init' if v['verilog_mode']=='flat' else 'riscv_mini__rom_rom.init'
    rom=output/'gateware'/name
    if v['boot_mode']=='rom':
        raw=Path(request['rom']).read_bytes();raw+=bytes((-len(raw))%4)
        expected=[f'{int.from_bytes(raw[i:i+4],"little"):08x}' for i in range(0,len(raw),4)]
        if not rom.is_file() or rom.read_text().lower().split()!=expected:
            raise ValueError('Generated ROM content differs from locked bootloader')
    elif rom.exists():raise ValueError('Unexpected ROM initialization in XIP gateware')
    return 0
