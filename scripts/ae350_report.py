"""Generate the current AE350 memory evidence summary from the operation receipts.

Nothing here is hand-written evidence: every claim points at a
``build/runs/<id>/result.json`` and its recorded artifact identities.  The
summary keeps pass / fail / waived / unsupported / untested as separate states.
"""
import json
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

OPS = {
    'board-ae350-upload': 'loader',
    'board-ae350-jtag-test': 'jtag',
    'board-ae350-ddr-suite': 'payload',
    'board-ae350-recover': 'recovery',
}


def latest(operation, artifact=None, payload=None, require_step=None):
    matches = []
    for path in ROOT.glob('build/runs/*-%s/result.json' % operation):
        result = json.loads(path.read_text(encoding='utf-8'))
        if artifact is not None and result.get('artifact') != artifact:
            continue
        if payload is not None and result.get('payload_artifact') != payload:
            continue
        if result.get('dry_run'):
            continue
        if require_step is not None and step(result, require_step) is None:
            continue
        result['_path'] = str(path.resolve())
        matches.append((path.parent.name, result))
    return max(matches, key=lambda item: item[0])[1] if matches else None


def step(result, name):
    for item in result.get('steps', []):
        if item.get('name') == name:
            return item
    return None


def classify(kind, result):
    if result is None:
        return dict(state='untested', receipt=None)
    receipt = result.get('_path')
    if not result.get('passed') and not (result.get('error') or result.get('cleanup_error')
                                        or result.get('session', {}).get('cleanup_errors')):
        return dict(state='in-progress', receipt=receipt)
    if kind == 'loader':
        cases = (step(result, 'negative') or {}).get('cases', [])
        cpu = step(result, 'cpu') or {}
        ok = bool(result.get('passed')) and cpu.get('phase') == 'executing'
        state = 'pass' if ok else 'fail'
        return dict(state=state, receipt=receipt, cases={c['case']: c['got'].strip() for c in cases},
                    cpu=dict(pc=cpu.get('pc'), mcause=cpu.get('mcause'), phase=cpu.get('phase')))
    if kind == 'payload':
        res = step(result, 'results') or {}
        return dict(state='pass' if result.get('passed') else 'fail', receipt=receipt,
                    checks=res.get('checks'), failures=res.get('failures'),
                    coverage=(step(result, 'coverage') or {}),
                    performance=step(result, 'performance'), stress=step(result, 'stress'))
    return dict(state='pass' if result.get('passed') else 'fail', receipt=receipt)


def verify_archive(path, artifact, payload):
    """Verify a local candidate archive without promoting it to a release."""
    path = Path(path).resolve()
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('artifact') != artifact or manifest.get('payload_artifact') != payload:
        raise ValueError('Archive belongs to different artifacts')
    if not manifest.get('complete') or not manifest.get('files'):
        raise ValueError('Archive is incomplete')
    required = {'source.zip', 'source.patch', 'README.md', 'evidence/host/result.json'}
    if not required <= manifest['files'].keys():
        raise ValueError('Archive is missing source or host evidence')
    from modular.storage import Store
    archived_store = Store(path.parent)
    archived_store.get(artifact, recursive=True)
    archived_store.get(payload, recursive=True)
    for name, expected in manifest['files'].items():
        file = (path.parent / name).resolve()
        if not file.is_relative_to(path.parent):
            raise ValueError('Archive path escapes its root')
        actual = hashlib.sha256(file.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError('Archive file differs: ' + name)
    actual_files = {p.relative_to(path.parent).as_posix() for p in path.parent.rglob('*')
                    if p.is_file() and p != path}
    if actual_files != set(manifest['files']):
        raise ValueError('Archive file inventory differs')
    return dict(manifest=str(path), files=len(manifest['files']),
                kind=manifest['kind'], source_commit=manifest['source_commit'],
                source_dirty=manifest['source_dirty'])


def summarize(artifact, payload=None, host_tests=None, performance=None, archive=None):
    """Summarize observed facts; a report is not a substitute for review."""
    results = {op: latest(op, artifact, payload if kind == 'payload' else None,
                          require_step='negative' if kind == 'loader' else None)
               if artifact is not None else None for op, kind in OPS.items()}
    entries = {op: dict(classify(kind, results[op]), artifact=(results[op] or {}).get('artifact'),
                        payload_artifact=(results[op] or {}).get('payload_artifact'))
               for op, kind in OPS.items()}
    acceptance = {'M%d' % i: dict(state='untested') for i in range(1, 10)}
    if artifact is None:
        return dict(generated=True, artifact=None, operations=entries, acceptance=acceptance)
    from modular.storage import Store
    from modular.ae350 import timing_report
    store = Store(ROOT)
    config = store.get(artifact, recursive=True)['config']
    if not config.get('ddr_profile'):
        raise ValueError('Retired monitor is not a current memory platform')
    if payload is not None:
        store.get(payload, recursive=True)
    timing = timing_report(store.path(artifact))
    jtag = results['board-ae350-jtag-test']
    ddr = results['board-ae350-ddr-suite']
    loader = results['board-ae350-upload']
    recovery = results['board-ae350-recover']
    if jtag and jtag.get('passed'):
        acceptance['M1'] = dict(state='pass', receipt=jtag['_path'],
            scope='ITCM identity, enabled hard ILM/DLM, autonomous local-memory POST')
    acceptance['M4'] = dict(state='pass', timing=timing, core_target_hz=config['core_hz'],
        data_hz=config['data_memory_clock_hz'], peripheral_hz=config['bus_hz'],
        ddr_ck_mhz=config['ddr_ck_mhz'], scope='selected operating point; no physical maximum claim')
    if ddr and ddr.get('passed'):
        observer = step(ddr, 'observer') or {}
        reads, writes = observer.get('read_counts', []), observer.get('write_counts', [])
        delta = lambda a,b: (a-b) & 0xffff
        if len(reads)==4 and delta(reads[1],reads[0])==1 and reads[2]==reads[1] and delta(reads[3],reads[2])==1:
            acceptance['M2'] = dict(state='pass', receipt=ddr['_path'], observer=observer,
                scope='cache-line refill/hit/invalidate observation; protocol edge cases require host regression')
        if len(writes)==3 and writes[0]==writes[1] and delta(writes[2],writes[1])==1:
            acceptance['M3'] = dict(state='pass', receipt=ddr['_path'], observer=observer,
                scope='one 32-byte cache write-back becomes one UI write; failure paths require host regression')
        coverage = step(ddr, 'coverage') or {}
        calibration_runs = []
        for path in ROOT.glob('build/runs/*-board-ae350-jtag-test/result.json'):
            run = json.loads(path.read_text(encoding='utf-8'))
            if run.get('artifact') == artifact and run.get('passed') and not run.get('dry_run'):
                calibration_runs.append(dict(receipt=str(path.resolve()), calibrations=run.get('calibrations', [])))
        calibrations = [c for run in calibration_runs for c in run['calibrations']]
        full = coverage.get('limit') == config['ddr_profile']['window_bytes'] and coverage.get('words_per_block') == 8
        repeated = len(calibrations) >= 10 and all(x.get('passed') for x in calibrations)
        acceptance['M7'] = dict(state='pass' if full and repeated and coverage.get('errors')==0 else 'partial',
            receipt=ddr['_path'], coverage=coverage, recalibration_receipt=(jtag or {}).get('_path'),
            recalibrations=len(calibrations), calibration_runs=calibration_runs)
        bandwidth, stress = step(ddr, 'bandwidth'), step(ddr, 'stress')
        acceptance['M8'] = dict(state='partial', receipt=ddr['_path'], bandwidth=bandwidth,
            performance=step(ddr,'performance'), stress=stress,
            scope='raw samples only; comparison baseline and frequency interpretation are separately reviewed')
    if loader and loader.get('passed'):
        negative = (step(loader,'negative') or {}).get('cases', [])
        acceptance['M6'] = dict(state='pass' if negative else 'partial', receipt=loader['_path'],
            scope='normal UART loader and recorded rejection cases', negative=negative)
    if recovery and recovery.get('passed'):
        acceptance['M9'] = dict(state='partial', receipt=recovery['_path'],
            scope='current platform recovery passed; source/artifact/evidence archive checked separately')
    acceptance['M5'] = dict(state='untested', scope='real FIFO/CDC, fault recovery and reset-release host regressions must be attached')
    if host_tests is not None:
        host_path = Path(host_tests)
        host = json.loads(host_path.read_text(encoding='utf-8'))
        expected = {'tests/ae350_cdc_test.py', 'tests/ae350_burst_test.py', 'tests/ae350_memory_test.py',
                    'scripts/tests/test_ddr3_ahb.py', 'scripts/tests/test_ae350_interconnect.py'}
        passed = {t['test'] for t in host.get('tests', []) if t.get('passed')}
        if host.get('passed') and expected <= passed:
            acceptance['M5'] = dict(state='pass', receipt=str(host_path.resolve()),
                scope='real FIFO/CDC, protocol failures, coordinated reset assertion and independent release; arbitrary one-sided hot reset unsupported')
    if performance is not None:
        perf_path = Path(performance)
        perf = json.loads(perf_path.read_text(encoding='utf-8'))
        if perf.get('candidate_artifact') != artifact or perf.get('payload_artifact') != payload:
            raise ValueError('Performance baseline belongs to different artifacts')
        if perf.get('passed'):
            acceptance['M8'].update(state='pass', comparison=str(perf_path.resolve()),
                scope='same-payload three-sample ILM benchmark, DDR-code dependent loads and host-timed short stress')
    if archive is not None:
        checked = verify_archive(archive, artifact, payload)
        if recovery and recovery.get('passed'):
            acceptance['M9'] = dict(state='pass', receipt=recovery['_path'], archive=checked,
                scope='current bootloader recovery, exact artifact dependency closure, source snapshot and evidence hashes; not a formal release')
    return dict(generated=True, source='scripts/ae350_report.py', artifact=artifact,
                payload_artifact=payload, operations=entries, acceptance=acceptance,
                scope='receipt-derived evidence; final engineering verdict includes host tests and archive review')


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', required=True, type=Path)
    parser.add_argument('--payload', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/ae350-memory-evidence.json')
    parser.add_argument('--host-tests', type=Path)
    parser.add_argument('--performance', type=Path)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    def reference(path):
        if path.is_dir():
            path = path / 'result.json'
        return json.loads(path.read_text(encoding='utf-8'))['artifact']
    summary = summarize(reference(args.build), reference(args.payload), args.host_tests, args.performance, args.archive)
    args.output.write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print('wrote', args.output)


if __name__ == '__main__':
    main()
