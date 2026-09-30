"""Generate the M0 SoC and compile a freestanding RV32IM UART ROM.

Default: no synthesis or programming. --synthesize runs Gowin PnR.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def checked(command, log=None):
    command = [str(part) for part in command]
    print('RUN', ' '.join(command), flush=True)
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('w', encoding='utf-8') as stream:
            result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            print(log.read_text(encoding='utf-8', errors='replace')[-8000:])
            raise SystemExit(f'Command failed; full log: {log}')
    else:
        subprocess.run(command, cwd=ROOT, check=True)


def generate(output, binary=None, synthesize=False, with_ddr=False, with_io=False, with_video=False, with_stress=False, experiment=None):
    from gateware.soc import MiniSoC
    from litex.soc.integration.builder import Builder
    data = None
    if binary:
        raw = binary.read_bytes()
        raw += bytes((-len(raw)) % 4)
        data = [int.from_bytes(raw[i:i+4], 'little') for i in range(0, len(raw), 4)]
    soc = MiniSoC(rom_data=data, with_ddr=with_ddr, with_io=with_io, with_video=with_video, with_stress=with_stress, experiment=experiment)
    builder = Builder(soc, output_dir=str(output), compile_software=False,
        compile_gateware=synthesize, csr_json=str(output / 'csr.json'), csr_csv=str(output / 'csr.csv'))
    builder.build(run=synthesize, build_name='riscv_mini')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--synthesize', action='store_true')
    parser.add_argument('--stage', choices=('m0', 'm1', 'm2', 'm3', 'm4'), default='m0')
    parser.add_argument('--generate-only', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--rom', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--experiment-config', type=Path, help='Explicit clock experiment JSON; requires a separate output directory')
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    output = args.output_dir.resolve() if args.output_dir else ROOT / 'build' / args.stage
    experiment=json.loads(args.experiment_config.read_text()) if args.experiment_config else None
    if experiment and (not args.output_dir or output in [ROOT/'build'/stage for stage in ('m0','m1','m2','m3','m4')]):
        parser.error('Clock experiments must use a separate --output-dir')
    experiment_args=['--output-dir',str(output)]
    if args.experiment_config: experiment_args += ['--experiment-config',str(args.experiment_config.resolve())]
    if args.generate_only:
        generate(output, args.rom, args.synthesize, args.stage != 'm0', args.stage in ('m2','m3','m4'), args.stage in ('m3','m4'), args.stage == 'm4', experiment)
        return
    tools = json.loads((ROOT / '.tools.local.json').read_text())
    gcc = Path(tools['gcc'])
    toolbin = gcc.parent
    os.environ['PATH'] = os.pathsep.join([str(toolbin), str(Path(tools['gowin']).parent), os.environ['PATH']])
    os.environ['PYTHONUTF8'] = '1'
    output.mkdir(parents=True, exist_ok=True)
    checked([sys.executable, __file__, '--generate-only', '--stage', args.stage,*experiment_args], output / 'generate.log')

    csr = json.loads((output / 'csr.json').read_text())
    rom_size=(48 if args.stage=='m4' else 32)*1024
    if csr['memories']['rom']['base'] != 0 or csr['memories']['rom']['size'] != rom_size:
        raise SystemExit('Generated ROM layout does not match firmware linker script.')
    if csr['memories']['sram']['base'] != 0x10000000 or csr['memories']['sram']['size'] != 16384:
        raise SystemExit('Generated SRAM layout does not match firmware linker script.')
    registers = csr['csr_registers']
    functions = []
    simple_csrs=['uart_rxtx', 'uart_txfull', 'uart_rxempty', 'uart_ev_pending']
    if experiment and args.stage=='m0':
        simple_csrs += ['timer0_en','timer0_load','timer0_reload','timer0_update_value','timer0_value']
    for name in simple_csrs:
        register = registers[name]
        if register['size'] != 1:
            raise SystemExit(f'Expected a single CSR word: {name}')
        address = register['addr']
        functions.append(f'static inline unsigned mini_{name}_read(void) {{ return *(volatile unsigned *)0x{address:08x}u; }}')
        functions.append(f'static inline void mini_{name}_write(unsigned value) {{ *(volatile unsigned *)0x{address:08x}u = value; }}')
    firmware = output / 'firmware'
    firmware.mkdir(exist_ok=True)
    (firmware / 'mini_csr.h').write_text('#pragma once\n' + '\n'.join(functions) + '\n', encoding='utf-8')
    extra = []
    if experiment:
        extra += ['-DMINI_FREQUENCY',
                  f'-DMINI_SYS_MHZ="{experiment["system_clock_hz"]/1e6:g}"',
                  f'-DMINI_DDR_DESCRIPTION="{2*experiment["system_clock_hz"]/1e6:g} MHz '+
                  ('DLL-off' if experiment.get('ddr_dll_off',True) else 'DLL-on')+
                  f' CL{experiment.get("cl",6)}/CWL{experiment.get("cwl",6)} ODT disabled"']
        if args.stage=='m0': extra += ['-I',output/'software/include']
    if args.stage != 'm0':
        if csr['memories']['main_ram'] != {'base': 0x40000000, 'size': 128*1024*1024, 'type': 'cached'}:
            raise SystemExit(f"Unexpected DDR layout: {csr['memories']['main_ram']}")
        # Standalone CSR accessors use the generated schema, including MSW-first
        # 64-bit DFII registers, without pulling in a libc-dependent BIOS.
        lines = ['#pragma once', '#include <stdint.h>']
        for name, reg in registers.items():
            count, addr = reg['size'], reg['addr']
            if count > 2:
                raise SystemExit(f'Unsupported CSR width: {name}')
            typ = 'uint64_t' if count == 2 else 'uint32_t'
            lines += [f'#define CSR_{name.upper()}_ADDR 0x{addr:08x}u',
                      f'#define CSR_{name.upper()}_SIZE {count}']
            reads = ' | '.join(f'(({typ})*(volatile uint32_t *)0x{addr+4*i:08x}u << {32*(count-i-1)})' for i in range(count))
            writes = ' '.join(f'*(volatile uint32_t *)0x{addr+4*i:08x}u = (uint32_t)(value >> {32*(count-i-1)});' for i in range(count))
            lines += [f'static inline {typ} {name}_read(void) {{ return {reads}; }}',
                      f'static inline void {name}_write({typ} value) {{ {writes} }}']
        include = firmware / 'include'
        (include / 'generated').mkdir(parents=True, exist_ok=True)
        (include / 'hw').mkdir(exist_ok=True)
        (include / 'generated/csr.h').write_text('\n'.join(lines)+'\n', encoding='utf-8')
        (include / 'hw/common.h').write_text('#pragma once\n#include <stdint.h>\n', encoding='utf-8')
        extra += ['-DMINI_DDR', '-I', include, '-I', output / 'software/include', ROOT / 'firmware/boot/ddr.c']
        if args.stage in ('m2','m3','m4'):
            vendor = ROOT / 'firmware/vendor/fatfs'
            extra += ['-DMINI_IO', '-I', vendor, '-I', ROOT / 'firmware/drivers',
                      *[ROOT / 'firmware/drivers' / name for name in
                        ('spi.c', 'lcd.c', 'sd.c', 'filesystem.c', 'string.c')],
                      vendor / 'ff.c', vendor / 'ffunicode.c']
        if args.stage in ('m3','m4'): extra += ['-DMINI_VIDEO', ROOT / 'firmware/drivers/video.c']
        if args.stage == 'm4': extra += ['-DMINI_STRESS']
        app = firmware / 'ddr-smoke.elf'
        checked([gcc, '-march=rv32im', '-mabi=ilp32', '-Os', '-ffreestanding', '-fno-builtin',
                 '-nostdlib', '-msmall-data-limit=0', ROOT / 'firmware/apps/ddr-smoke.c',
                 '-Wl,-Ttext=0x40100000,-e,ddr_smoke', '-o', app])
        app_bin = firmware / 'ddr-smoke.bin'
        checked([toolbin / 'riscv-none-elf-objcopy.exe', '-O', 'binary', app, app_bin])
        (firmware / 'ddr_app.h').write_text('static const unsigned char ddr_app[] = {'+
            ','.join(str(b) for b in app_bin.read_bytes())+'};\n', encoding='utf-8')
    flags = ['-march=rv32im', '-mabi=ilp32', '-Os', '-Wall', '-Wextra', '-Werror', '-ffreestanding', '-fno-builtin',
        '-ffunction-sections', '-fdata-sections', '-nostdlib', '-nostartfiles', '-msmall-data-limit=0']
    linker=firmware/'linker.ld'
    linker.write_text((ROOT/'firmware/boot/linker.ld').read_text().replace('LENGTH = 32K',f'LENGTH = {rom_size//1024}K'),encoding='utf-8')
    elf = firmware / 'boot.elf'
    main_source=ROOT/'firmware/apps/frequency-cpu.c' if experiment and args.stage=='m0' else ROOT/'firmware/boot/main.c'
    checked([gcc, *flags, *extra, '-I', firmware, ROOT / 'firmware/boot/start.S', main_source,
        '-T', linker, '-Wl,--gc-sections', f'-Wl,-Map,{firmware / "boot.map"}', '-lgcc', '-o', elf])
    cpp = firmware / 'toolchain-smoke.o'
    checked([toolbin / 'riscv-none-elf-g++.exe', *flags, '-fno-exceptions', '-fno-rtti', '-c',
        ROOT / 'firmware/apps/toolchain-smoke.cpp', '-o', cpp])
    checked([gcc, '-march=rv32im', '-mabi=ilp32', '-nostdlib', cpp,
        '-Wl,-e,mini_cpp_smoke', '-lgcc', '-o', firmware / 'toolchain-smoke.elf'])
    binary = firmware / 'boot.bin'
    checked([toolbin / 'riscv-none-elf-objcopy.exe', '-O', 'binary', elf, binary])
    size = binary.stat().st_size
    if size > rom_size:
        raise SystemExit(f'Boot ROM overflow: {size} > {rom_size}')
    checked([toolbin / 'riscv-none-elf-size.exe', elf])
    attributes = subprocess.check_output([toolbin / 'riscv-none-elf-readelf.exe', '-h', '-A', elf], text=True)
    (firmware / 'elf-info.txt').write_text(attributes, encoding='utf-8')
    command = [sys.executable, __file__, '--generate-only', '--stage', args.stage, '--rom', binary,*experiment_args]
    if args.synthesize:
        command += ['--synthesize']
    checked(command, output / ('synthesis.log' if args.synthesize else 'generate-rom.log'))
    rom_words = (output / 'gateware/riscv_mini_rom.init').read_text().split()
    raw = binary.read_bytes()
    expected = [f'{int.from_bytes(raw[i:i+4], "little"):08x}' for i in range(0, len(raw), 4)]
    if [word.lower() for word in rom_words] != expected:
        raise SystemExit('Generated ROM contents do not match boot.bin.')
    report = {
        'profile': {'m0':'CPU/UART/timer/ROM/SRAM', 'm1':'CPU + 128 MiB DDR',
                    'm2':'CPU + DDR + SPI LCD + SPI SD + FatFs; no HDMI',
                    'm3':'CPU + DDR + SD/SPI LCD + 480x272 RGB LCD DMA; no HDMI',
                    'm4':'M3 + DDR copy/check stress monitor; no HDMI'}[args.stage],
        'rom_size_bytes': rom_size, 'firmware_bytes': size, 'firmware_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'isa': 'rv32im', 'abi': 'ilp32', 'clock_hz': experiment['system_clock_hz'] if experiment else 60000000 if args.stage=='m4' else 48000000,
        'rtl': str(output / 'gateware/riscv_mini.v'),
        'synthesis_requested': args.synthesize,
        'board_test': 'not performed',
    }
    if args.synthesize:
        bitstream = output / 'gateware/riscv_mini.fs'
        if not bitstream.is_file() or not bitstream.stat().st_size:
            raise SystemExit('Gowin did not produce a nonempty bitstream.')
        class ReportText(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts = []
            def handle_data(self, text):
                if text.strip():
                    self.parts.append(text.strip())
        timing = ReportText()
        timing.feed((output / 'gateware/impl/pnr/project_tr_content.html').read_text(errors='replace'))
        text = ' '.join(timing.parts)
        counts = {}
        for kind in ('Setup', 'Hold'):
            match = re.search(f'Numbers of {kind} Violated Endpoints (\\d+)', text)
            if not match:
                raise SystemExit(f'Cannot verify {kind} timing from Gowin report.')
            counts[kind.lower()] = int(match.group(1))
        resource_text = (output / 'gateware/impl/pnr/project.rpt.txt').read_text(errors='replace')
        resources = {}
        for kind in ('Logic', 'Register', 'BSRAM'):
            match = re.search(rf'^\s*{kind}\s*\|\s*(\d+)/(\d+)', resource_text, re.MULTILINE)
            if match:
                resources[kind] = {'used': int(match.group(1)), 'available': int(match.group(2))}
        report['bitstream'] = str(bitstream)
        report['bitstream_sha256'] = hashlib.sha256(bitstream.read_bytes()).hexdigest()
        report['timing_violated_endpoints'] = counts
        report['resources'] = resources
    if experiment: report['experiment']=experiment
    (output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if args.synthesize and any(report['timing_violated_endpoints'].values()):
        raise SystemExit(f'Gowin reports timing violations: {report["timing_violated_endpoints"]}; not eligible for programming')
    print(f'{args.stage} build verified: {size} bytes of ROM firmware. Report: {output / "validation.json"}')


if __name__ == '__main__':
    main()
