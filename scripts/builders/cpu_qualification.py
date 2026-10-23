"""Resource/timing experiment with an external CPU RTL; never programs hardware.

The accepted boot ROM is embedded only to retain realistic ROM utilization.
It is not adapted to data caches, FPU state or virtual memory. These outputs
are deliberately qualification reports, not deployable validation manifests.
"""
import hashlib
import json
import os
import re
import subprocess
import shutil
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from gateware.machines.primer20k_vexriscv.features import Features
from gateware.machines.primer20k_vexriscv.config import SD_PROFILES, storage_profile, cpu_configuration, pll_count
from scripts.modular.machines.primer20k_vexriscv.rtl import pnr_report


def build(args, run=None):
    from gateware.description import from_snapshot
    from scripts.modular.environment import discover
    machine = from_snapshot(args.resolved_machine_snapshot)
    settings = machine.rtl
    vendor = discover(['gowin'])['bindings']['gowin'][0]
    os.environ['PATH'] = str(Path(vendor).parent) + os.pathsep + os.environ.get('PATH', '')
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cpu = ROOT / settings.cpu_verilog
    rom = args.rom.read_bytes()
    rom += bytes((-len(rom)) % 4)
    data = [int.from_bytes(rom[i:i+4], 'little') for i in range(0, len(rom), 4)]
    features = Features(**settings.features)
    args.sd_profile = settings.sd_profile
    args.sd_backend = 'native' if args.sd_profile in ('lite', 'full') else args.sd_profile
    args.cpu_variant = settings.cpu_variant
    args.usb_backend, args.audio_clock = settings.usb_backend, settings.audio_clock
    args.place_option, args.route_option = settings.place_option, settings.route_option
    args.l2_size, args.rom_size, args.boot_mode = settings.l2_size_bytes, machine.boot.rom_size_bytes, machine.boot.mode
    from gateware.machines.primer20k_vexriscv.config import cpu_capabilities
    dcache = cpu_capabilities(cpu)['dcache']
    expected_plls = pll_count(features, args.usb_backend, args.audio_clock)
    report = {
        'cpu_rtl': str(cpu), 'cpu_sha256': hashlib.sha256(cpu.read_bytes()).hexdigest(),
        'sd_backend':args.sd_backend, 'cpu_variant_adapter': args.cpu_variant, 'usb_backend':args.usb_backend, 'features': features.as_dict(),
        'sd_profile':args.sd_profile,'audio_clock':args.audio_clock,'expected_plls':expected_plls,
        'cpu_capabilities':{'mmu':features.mmu,'fpu':features.fpu,'dcache':dcache},
        'pnr_place_option':args.place_option,
        'pnr_route_option':args.route_option,
        'pipeline_configuration':json.loads((cpu.parent/'generator.json').read_text()).get('pipeline'),
        'sys_hz': machine.clocks.sys, 'ddr_hz': machine.clocks.sys * 2,
        'l2_size_bytes':args.l2_size,'rom_size_bytes':args.rom_size,'boot_mode':args.boot_mode,
        'rom_source': str(args.rom.resolve()), 'rom_sha256': hashlib.sha256(rom).hexdigest(),
        'software_adapted': False, 'board_test': 'not performed',
        'purpose': 'resource and timing qualification only; not a deployable build',
        'completed': False,
    }
    error = None
    local = json.loads((ROOT/'.tools.local.json').read_text())
    os.environ['PATH'] = str(Path(local['gowin']).parent)+os.pathsep+os.environ['PATH']
    try:
        from scripts.modular.machines.primer20k_vexriscv.rtl import generate
        generate(output, binary=args.rom, machine=machine)
        if args.synthesis_only:
            run_tcl=output/'gateware/run.tcl'
            script=run_tcl.read_text()
            if script.count('run all')!=1:raise ValueError('Expected one run all in generated Tcl')
            run_tcl.write_text(script.replace('run all','run syn'))
        from scripts.modular.gowin import run_with_license_retry
        from scripts.modular.workspace import invalidate_pnr
        environment = os.environ.copy()
        library = Path(vendor).resolve().parent.parent / 'lib'
        if library.is_dir():
            names = ('DYLD_LIBRARY_PATH', 'DYLD_FRAMEWORK_PATH') if sys.platform == 'darwin' else ('LD_LIBRARY_PATH',)
            for name in names:
                environment[name] = str(library) + (os.pathsep + environment[name] if environment.get(name) else '')
        def attempt(log_path):
            invalidate_pnr(output)
            with log_path.open('w', encoding='utf-8') as log:
                return subprocess.run([vendor, 'run.tcl'], cwd=output / 'gateware', env=environment,
                                      stdout=log, stderr=subprocess.STDOUT).returncode
        returncode, log = run_with_license_retry(attempt, run.path if run else output, prefix='qualification')
        shutil.copyfile(log, output / ('synthesis-only.log' if args.synthesis_only else 'gowin.log'))
        if returncode:
            raise subprocess.CalledProcessError(returncode, [vendor, 'run.tcl'])
        if args.synthesis_only:
            report.update(completed=True,stage='synthesis_only')
        else:
            shutil.copyfile(output / 'gateware/impl/pnr/project.fs', output / 'gateware/riscv_mini.fs')
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
