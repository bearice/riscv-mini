"""Resource/timing experiment with an external CPU RTL; never programs hardware.

The accepted boot ROM is embedded only to retain realistic ROM utilization.
It is not adapted to data caches, FPU state or virtual memory. These outputs
are deliberately qualification reports, not deployable validation manifests.
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
from gateware.features import Features
from gateware.config import SD_PROFILES, storage_profile, cpu_configuration, pll_count
from gateware.soc import MiniSoC
from gateware.rtl import split_verilog
from litex.soc.integration.builder import Builder
from scripts.build import pnr_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpu-verilog', required=True, type=Path)
    parser.add_argument('--cpu-variant', choices=('lite','full','linux'), default=None)
    parser.add_argument('--usb-backend',choices=('ohci','ultra'),default='ultra')
    parser.add_argument('--sd-backend', choices=('native','spi'), default=None)
    parser.add_argument('--sd-profile',choices=SD_PROFILES,default=None)
    parser.add_argument('--audio-clock',choices=('dds','sys','legacy'),default='dds')
    parser.add_argument('--profile', choices=('full', 'minimal'), default='full')
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--rom', type=Path, default=ROOT/'build/base/firmware/boot.bin',
                        help='ROM image used to keep realistic ROM resource usage; build it first with scripts/build.py')
    parser.add_argument('--without', action='append', choices=Features.names(), default=[])
    parser.add_argument('--retiming', action='store_true',
                        help='Emit set_option -retiming 1; verify native Gowin synthesis settings before claiming it is active')
    parser.add_argument('--pipelining', action='store_true',
                        help='Emit set_option -pipe 1; verify native Gowin synthesis settings before claiming it is active')
    parser.add_argument('--retiming-resource', choices=('none','all','bsram','dsp'),
                        default='none', help='PnR retiming target resources')
    parser.add_argument('--place-option', type=int, choices=range(5), default=2,
                        help='Gowin PnR placement algorithm (default: timing priority)')
    parser.add_argument('--route-option', type=int, choices=range(3), default=1,
                        help='Gowin PnR routing algorithm (default: timing priority)')
    parser.add_argument('--synthesis-only', action='store_true',
                        help='Run synthesis without placement and routing')
    args = parser.parse_args()
    args.audio_clock = "dds" if args.audio_clock == "sys" else args.audio_clock
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cpu = args.cpu_verilog.resolve()
    rom = args.rom.read_bytes()
    rom += bytes((-len(rom)) % 4)
    data = [int.from_bytes(rom[i:i+4], 'little') for i in range(0, len(rom), 4)]
    overrides={name: False for name in args.without}
    features = Features.resolve(args.profile, overrides)
    args.sd_profile,features=storage_profile(args.sd_profile,args.sd_backend,features,overrides)
    args.sd_backend='native' if args.sd_profile in ('lite','full') else args.sd_profile
    features,args.cpu_variant,dcache=cpu_configuration(features,overrides,cpu,args.cpu_variant)
    expected_plls=pll_count(features,args.usb_backend,args.audio_clock)
    report = {
        'cpu_rtl': str(cpu), 'cpu_sha256': hashlib.sha256(cpu.read_bytes()).hexdigest(),
        'sd_backend':args.sd_backend, 'cpu_variant_adapter': args.cpu_variant, 'usb_backend':args.usb_backend, 'features': features.as_dict(),
        'sd_profile':args.sd_profile,'audio_clock':args.audio_clock,'expected_plls':expected_plls,
        'cpu_capabilities':{'mmu':features.mmu,'fpu':features.fpu,'dcache':dcache},
        'synthesis_retiming_requested':args.retiming,
        'synthesis_pipelining_requested':args.pipelining,
        'pnr_retiming_resource':args.retiming_resource,
        'pnr_place_option':args.place_option,
        'pnr_route_option':args.route_option,
        'pipeline_configuration':json.loads((cpu.parent/'generator.json').read_text()).get('pipeline'),
        'sys_hz': 60000000, 'ddr_hz': 120000000,
        'rom_source': str(args.rom.resolve()), 'rom_sha256': hashlib.sha256(rom).hexdigest(),
        'software_adapted': False, 'board_test': 'not performed',
        'purpose': 'resource and timing qualification only; not a deployable build',
        'completed': False,
    }
    error = None
    local = json.loads((ROOT/'.tools.local.json').read_text())
    os.environ['PATH'] = str(Path(local['gowin']).parent)+os.pathsep+os.environ['PATH']
    try:
        soc = MiniSoC(rom_data=data, features=features,
                      cpu_variant=args.cpu_variant, cpu_verilog=cpu,usb_backend=args.usb_backend,sd_backend=args.sd_backend,sd_profile=args.sd_profile,audio_clock=args.audio_clock)
        if args.retiming:soc.platform.toolchain.options['retiming']='1'
        if args.pipelining:soc.platform.toolchain.options['pipe']='1'
        soc.platform.toolchain.options['retiming_resource']=args.retiming_resource
        soc.platform.toolchain.options['place_option']=str(args.place_option)
        soc.platform.toolchain.options['route_option']=str(args.route_option)
        split_verilog(soc.platform)
        builder = Builder(soc, output_dir=str(output), compile_software=False,
                          compile_gateware=True, csr_json=str(output/'csr.json'),
                          csr_csv=str(output/'csr.csv'), hierarchical=True)
        builder.build(run=not args.synthesis_only, build_name='riscv_mini')
        if args.synthesis_only:
            run_tcl=output/'gateware/run.tcl'
            script=run_tcl.read_text()
            if script.count('run all')!=1:raise ValueError('Expected one run all in generated Tcl')
            run_tcl.write_text(script.replace('run all','run syn'))
            subprocess.run([local['gowin'],'run.tcl'],cwd=output/'gateware',check=True,
                           stdout=(output/'synthesis-only.log').open('w'),stderr=subprocess.STDOUT)
            report.update(completed=True,stage='synthesis_only')
        else:
            timing, resources = pnr_report(output)
            report.update(completed=True, stage='pnr',timing_violated_endpoints=timing, resources=resources,
                          timing_passed=not any(timing.values()))
            fs = output/'gateware/riscv_mini.fs'
            report['bitstream_sha256'] = hashlib.sha256(fs.read_bytes()).hexdigest()
    except Exception as caught:
        error = caught
        report['failure'] = str(caught)
    synthesis_log = output/'gateware/impl/gwsynthesis/project.log'
    if synthesis_log.is_file():
        log_text = synthesis_log.read_text(errors='replace')
        report['synthesis_errors'] = re.findall(r'^ERROR[^\r\n]*', log_text, re.M)
        overflow = re.search(r'The number\((\d+)\([^\r\n]*?logic[^\r\n]*?limit\((\d+)\)', log_text)
        if overflow:
            report['capacity_failure'] = {
                'resource': 'Logic', 'used': int(overflow[1]), 'available': int(overflow[2]),
            }
    routing_log = output/'gateware/impl/pnr/project.log'
    if routing_log.is_file():
        log_text = routing_log.read_text(errors='replace')
        report['pnr_errors'] = re.findall(r'^ERROR[^\r\n]*', log_text, re.M)
        unrouted = re.search(r'total (\d+) unrouted nets', log_text)
        if unrouted:
            report['unrouted_nets'] = int(unrouted[1])
    # A capacity failure may stop before PnR. Keep the synthesis resource report.
    class ReportText(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
        def handle_data(self, text):
            if text.strip():
                self.parts.append(text.strip())

    for stage, path in [('synthesis', output/'gateware/impl/gwsynthesis/project_syn.rpt.html'),
                        ('pnr', output/'gateware/impl/pnr/project.rpt.txt')]:
        if path.is_file():
            text = path.read_text(errors='replace')
            usage = {}
            if stage == 'synthesis':
                parsed = ReportText()
                parsed.feed(text)
                for index, value in enumerate(parsed.parts):
                    match = re.fullmatch(r'(\d+)(?:\([^)]*\))?\s*/\s*(\d+)', value)
                    if match and index:
                        usage[parsed.parts[index-1]] = {'used': int(match[1]), 'available': int(match[2])}
            else:
                for match in re.finditer(r'^\s*([^|\r\n]+?)\s*\|\s*(\d+(?:\.\d+)?)/(\d+)', text, re.M):
                    used = float(match[2]) if '.' in match[2] else int(match[2])
                    usage[match[1].strip()] = {'used': used, 'available': int(match[3])}
            report[stage+'_report'] = str(path)
            report[stage+'_resources'] = usage
    (output/'qualification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2), flush=True)
    if error:
        raise error


if __name__ == '__main__':
    main()
