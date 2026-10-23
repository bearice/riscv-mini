"""RTL elaboration, CSR headers and vendor report parsing shared by every entry point."""
import re
from pathlib import Path
from html.parser import HTMLParser

def generate(output, binary=None, synthesize=False, sd_backend=None, features=None,
             hierarchical=None, usb_backend=None, cpu_variant=None, cpu_verilog=None,
             sd_profile=None, audio_clock=None, deep_verilog=None, place_option=None,
             route_option=None, l2_size=None, rom_size=None, boot_mode=None, machine=None):
    from gateware.description import resolve_machine
    from gateware.machines.primer20k_vexriscv.features import Features
    from gateware.machines.primer20k_vexriscv.soc import MiniSoC
    from pathlib import Path
    import hashlib
    if machine is None:
        raise ValueError('RTL generation requires an explicit machine snapshot')
    config = machine
    settings = config.rtl
    hierarchy = settings.rtl_hierarchy
    expected = dict(sd_backend='native' if settings.sd_profile in ('lite', 'full') else 'spi' if settings.sd_profile=='spi' else 'none',
        features=Features(**settings.features), hierarchical=hierarchy != 'flat',
        deep_verilog=hierarchy == 'deep', usb_backend=settings.usb_backend,
        cpu_variant=settings.cpu_variant, sd_profile=settings.sd_profile,
        audio_clock=settings.audio_clock, place_option=settings.place_option,
        route_option=settings.route_option, l2_size=settings.l2_size_bytes,
        rom_size=config.boot.rom_size_bytes, boot_mode=config.boot.mode)
    given = dict(sd_backend=sd_backend, features=features, hierarchical=hierarchical,
        deep_verilog=deep_verilog, usb_backend=usb_backend, cpu_variant=cpu_variant,
        sd_profile=sd_profile, audio_clock=audio_clock, place_option=place_option,
        route_option=route_option, l2_size=l2_size, rom_size=rom_size, boot_mode=boot_mode)
    for key, value in given.items():
        if value is not None and value != expected[key]:
            raise ValueError('RTL parameter differs from machine.json: ' + key)
    source_cpu = Path(__file__).resolve().parents[4] / settings.cpu_verilog if settings.cpu_verilog else None
    if cpu_verilog is not None:
        if source_cpu is None or hashlib.sha256(Path(cpu_verilog).read_bytes()).digest() != hashlib.sha256(source_cpu.read_bytes()).digest():
            raise ValueError('CPU RTL differs from machine.json selection')
    else:
        cpu_verilog = source_cpu
    sd_backend, features, hierarchical = expected['sd_backend'], expected['features'], expected['hierarchical']
    deep_verilog, usb_backend, cpu_variant = expected['deep_verilog'], expected['usb_backend'], expected['cpu_variant']
    sd_profile, audio_clock = expected['sd_profile'], expected['audio_clock']
    place_option, route_option = expected['place_option'], expected['route_option']
    l2_size, rom_size, boot_mode = expected['l2_size'], expected['rom_size'], expected['boot_mode']
    from litex.soc.integration.builder import Builder
    data=None
    if binary:
        raw=binary.read_bytes();raw+=bytes((-len(raw))%4)
        data=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)]
    soc=MiniSoC(machine=config, rom_data=data, cpu_verilog=cpu_verilog)
    soc.platform.toolchain.options.update(place_option=place_option,route_option=route_option)
    if hierarchical:
        from gateware.machines.primer20k_vexriscv.rtl import split_verilog
        split_verilog(soc.platform,deep=deep_verilog)
    builder=Builder(soc,output_dir=str(output),compile_software=False,compile_gateware=synthesize,
                    csr_json=str(output/'csr.json'),csr_csv=str(output/'csr.csv'),hierarchical=hierarchical)
    builder.build(run=synthesize,build_name='riscv_mini')

def export_support():
    """Installed LiteX inputs used by the software export action."""
    import litex
    base = Path(litex.__file__).parent
    names = ('soc/integration/export.py', 'soc/software/include/hw/common.h',
             'soc/cores/cpu/vexriscv/system.h', 'soc/cores/cpu/vexriscv/csr-defs.h')
    return {name: base / name for name in names}


def generate_csr(csr, include):
    """Use LiteX exports, then append only project compatibility accessors."""
    import json
    import tempfile
    import litex
    from litex.soc.integration import export
    from scripts.modular.storage import text_file
    include = Path(include)
    include.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=include.parent) as temporary:
        source = Path(temporary) / 'csr.json'
        source.write_text(json.dumps(csr), encoding='utf-8')
        regions, constants, memories = export.load_csr_json(source)
    # The project aliases follow LiteX's closing guard; protect the complete
    # file as drivers may include csr.h through several public headers.
    lines = ['#pragma once', export.get_csr_header(regions, constants)]
    text_file(include / 'generated/soc.h', export.get_soc_header(constants))
    text_file(include / 'generated/mem.h', export.get_mem_header(memories))
    software = Path(litex.__file__).parent / 'soc/software'
    for name, source in (
        ('hw/common.h', software / 'include/hw/common.h'),
        ('system.h', software.parent / 'cores/cpu/vexriscv/system.h'),
        ('csr-defs.h', software.parent / 'cores/cpu/vexriscv/csr-defs.h'),
    ):
        text_file(include / name, source.read_text(encoding='utf-8'))
    for name, reg in csr['csr_registers'].items():
        if reg['size'] > 2:
            lines.append(f'static inline uint32_t {name}_read_word(unsigned index) {{ return csr_read_simple(CSR_{name.upper()}_ADDR + 4*index); }}')
    # Read-only compatibility accessors share the gateware layout definition.
    from gateware.machines.primer20k_vexriscv.csr_layout import STATUS_LAYOUTS
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
    (include/'generated/csr.h').write_text('\n'.join(lines)+'\n',encoding='utf-8')

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
    from scripts.modular.storage import read
    from gateware.machines.primer20k_vexriscv.features import Features
    request = read(request_path); v = request['validation']
    from gateware.description import from_snapshot
    machine = from_snapshot(v['machine_snapshot'])
    from pathlib import Path
    generate(Path(request['output']), Path(request['rom']), request['synthesize'],
             sd_backend=v['sd_backend'], features=Features(**v['features']),
             hierarchical=v['verilog_mode'] != 'flat', usb_backend=v['usb_backend'],
             cpu_variant=v['cpu_variant'], cpu_verilog=Path(request['cpu']) if request['cpu'] else None,
             sd_profile=v['sd_profile'], audio_clock=v['audio_clock'],
             deep_verilog=v['rtl_hierarchy'] == 'deep', place_option=v['place_option'],
             route_option=v['route_option'], l2_size=v['l2_size_bytes'],
             rom_size=v['rom_size_bytes'] or 8192, boot_mode=v['boot_mode'], machine=machine)
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
