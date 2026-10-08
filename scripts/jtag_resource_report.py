"""Capture synthesis/PnR evidence for the three --debug-mode experiments."""
import argparse
import hashlib
import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path


class Cells(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr': self.row = []
        if tag in ('td', 'th'): self.cell = ''

    def handle_data(self, data):
        if self.cell is not None: self.cell += data

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.row is not None and self.cell is not None:
            self.row.append(self.cell.strip())
            self.cell = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence(directory):
    gateware = directory/'gateware'
    report = gateware/'impl/gwsynthesis/project_syn.rpt.html'
    parser = Cells()
    parser.feed(report.read_text(errors='replace'))
    resources = {}
    for row in parser.rows:
        if len(row) == 2 and row[0] in ('Register', 'LUT', 'ALU', 'SSRAM', 'BSRAM'):
            if row[1].isdigit(): resources[row[0]] = int(row[1])
    if not {'Register', 'LUT', 'ALU', 'SSRAM'} <= resources.keys():
        raise ValueError(f'Missing synthesis counts in {report}')
    log = gateware/'impl/gwsynthesis/project.log'
    if 'ERROR' in log.read_text(errors='replace'):
        raise ValueError(f'Synthesis errors in {log}')
    files = [gateware/'riscv_mini.v', *sorted((gateware/'modules').glob('*.v'))]
    result = dict(path=str(directory.resolve()), synthesis=resources,
                  synthesis_report_sha256=digest(report),
                  rtl_sha256={str(p.relative_to(directory)): digest(p) for p in files},
                  constraints_sha256=digest(gateware/'riscv_mini.sdc'))
    project = (gateware/'run.tcl').read_text()
    sources = []
    for line in project.splitlines():
        if line.startswith('add_file '):
            path = Path(line.removeprefix('add_file ').replace('\\\\', '\\'))
            if not path.is_absolute(): path = gateware/path
            if path.is_file(): sources.append({'path': str(path.resolve()), 'sha256': digest(path)})
    result['sources'] = sources
    identity = directory/'build-info.json'
    if identity.exists():
        result['build_info'] = json.loads(identity.read_text())
    pnr = gateware/'impl/pnr/project.rpt.txt'
    if pnr.exists():
        text = pnr.read_text(errors='replace')
        result['pnr_resources'] = {
            name: {'used': int(used), 'available': int(available)}
            for name, used, available in re.findall(
                r'^\s*(Logic|Register|CLS|BSRAM)\s*\|\s*(\d+)/(\d+)', text, re.M)}
        result['pnr_report_sha256'] = digest(pnr)
    result['bitstream_generated'] = (gateware/'impl/pnr/project.fs').exists()
    pnr_log = gateware/'impl/pnr/project.log'
    if pnr_log.exists():
        pnr_text = pnr_log.read_text(errors='replace')
        result['pnr_errors'] = re.findall(r'^.*ERROR.*$', pnr_text, re.M)
        result['pnr_clock_warnings'] = re.findall(r'^.*TA1132.*$', pnr_text, re.M)
    timing = gateware/'impl/pnr/project_tr_content.html'
    if timing.exists():
        text = re.sub(r'<[^>]+>', ' ', timing.read_text(errors='replace'))
        result['timing_violations'] = {name.lower(): int(count) for name, count in re.findall(
            r'Numbers of (Setup|Hold) Violated Endpoints\s+(\d+)', text)}
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path('build'))
    p.add_argument('--output', type=Path, default=Path('build/jtag-resources.json'))
    args = p.parse_args()
    result = {'source_commit': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'configuration': {'profile': 'full', 'isa': 'rv32imaf', 'boot_mode': 'xip',
                          'rom_bytes': 0, 'l2_bytes': 4096, 'sys_hz': 60000000,
                          'icache_bytes': 2048, 'dcache_bytes': 2048,
                          'hardware_breakpoints': 0, 'jtag_hz': 6000000,
                          'place_option': 3, 'route_option': 2},
        'board_test': 'not performed', 'variants': {}}
    for name in ('baseline', 'transport', 'debug'):
        result['variants'][name] = evidence(args.root/('resource-'+name))
    captured_commits = {v['build_info']['source']['commit']
                        for v in result['variants'].values() if 'build_info' in v}
    if len(captured_commits) > 1:
        raise ValueError('Experiment variants do not share a source commit')
    if captured_commits:
        result['report_generator_commit'] = result['source_commit']
        result['source_commit'] = captured_commits.pop()
    base = result['variants']['baseline']['synthesis']
    for name in ('transport', 'debug'):
        values = result['variants'][name]['synthesis']
        result['variants'][name]['synthesis_delta'] = {
            k: v-base[k] for k, v in values.items() if k in base}
        if 'pnr_resources' in result['variants']['baseline'] and 'pnr_resources' in result['variants'][name]:
            result['variants'][name]['pnr_delta'] = {
                k: v['used']-result['variants']['baseline']['pnr_resources'][k]['used']
                for k, v in result['variants'][name]['pnr_resources'].items()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k: v['synthesis'] for k, v in result['variants'].items()}, indent=2))


if __name__ == '__main__': main()
