"""Generate the AE350 P2 acceptance summary from the operation receipts.

Nothing here is hand-written evidence: every claim points at a
``build/runs/<id>/result.json`` and its recorded artifact identities.  The
summary keeps pass / fail / waived / unsupported / untested as separate states.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

OPS = {
    'board-ae350-upload': 'loader',
    'board-ae350-jtag-test': 'jtag',
    'board-ae350-ddr-suite': 'payload',
    'board-ae350-verify': 'p1',
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


def summarize(artifact, payload=None):
    results = {op: latest(op, artifact, payload if kind in ('loader', 'payload') else None,
                          require_step='cpu' if kind == 'loader' else None) if artifact is not None else None
               for op, kind in OPS.items()}
    entries = {op: dict(classify(kind, results[op]),
                        artifact=(results[op] or {}).get('artifact'),
                        payload_artifact=(results[op] or {}).get('payload_artifact'))
               for op, kind in OPS.items()}
    acceptance = {('G%d' % i): dict(state='untested') for i in range(1, 12)}
    config = None
    # Artifact integrity and timing are independently checked, never inferred
    # from the presence of an unrelated successful hardware receipt.
    if artifact is not None:
        from modular.storage import Store
        from modular.ae350 import timing_report
        store = Store(ROOT)
        try:
            config = store.get(artifact, recursive=True)['config']
            acceptance['G1'] = dict(state='pass', artifact=artifact)
        except (ValueError, OSError, KeyError) as error:
            acceptance['G1'] = dict(state='fail', reason=str(error))
        else:
            try:
                timing = timing_report(store.path(artifact))
                acceptance['G2'] = dict(state='pass', timing=timing)
            except (ValueError, OSError, KeyError) as error:
                acceptance['G2'] = dict(state='fail', reason=str(error))
        if payload is not None:
            manifest = store.get(payload, recursive=True)
            if manifest['config'].get('machine') != 'mega138k_ae350':
                raise ValueError('Selected payload is not an AE350 artifact')
    ddr = results['board-ae350-ddr-suite']
    if ddr is not None:
        state = entries['board-ae350-ddr-suite']['state']
        receipt = ddr['_path']
        request = step(ddr, 'request') or {}
        if request.get('flags', 0) & 1:
            acceptance['G4'] = dict(state=state, receipt=receipt, scope='capacity/address-alias tags; not an exhaustive memory sweep')
        acceptance['G5'] = dict(state=state, receipt=receipt)
        coverage = step(ddr, 'coverage')
        observer = step(ddr, 'observer')
        if coverage:
            covered_state = state if coverage.get('errors') == 0 and coverage.get('blocks', 0) > 0 else 'fail'
            total_bytes = (config or {}).get('ddr_profile', {}).get('window_bytes')
            if covered_state == 'pass' and (coverage.get('limit') != total_bytes or coverage.get('words_per_block') != 8):
                covered_state = 'partial'
            acceptance['G6'] = dict(state=covered_state, receipt=receipt, coverage=coverage,
                                    scope='actual tested range and lanes only')
        if observer:
            acceptance['G7'] = dict(state=state if observer.get('errors') == 0 else 'fail', receipt=receipt, observer=observer)
        if state == 'pass' and acceptance['G2']['state'] == 'pass' and config is not None:
            acceptance['G10'] = dict(state='pass', receipt=receipt, ddr_ck_mhz=config.get('ddr_ck_mhz'),
                                     fabric_hz=config.get('bus_hz'),
                                     scope='selected delivery point only; CPU target is not an independent frequency measurement')
        acceptance['G9'] = dict(state='untested', short_regression=step(ddr, 'stress'),
                                scope='long-duration pressure not repeated on this artifact')
    loader = entries['board-ae350-upload']
    if loader['state'] == 'pass':
        acceptance['G8'] = dict(state='pass', receipt=loader['receipt'],
                                scope='payload execution only; no atomic support claim')
    acceptance['G11'] = dict(entries['board-ae350-recover'],
                            scope='selected P2 ROM recovery; standalone P1 image not requalified')
    if acceptance['G11']['state'] == 'pass':
        acceptance['G11'].update(state='partial', rom_recovery='pass')
    handoff = latest('board-ae350-upload', artifact, payload) if artifact is not None else None
    if handoff is not None:
        entries['uart_handoff'] = dict(state='pass' if handoff.get('passed') else 'fail',
                                      receipt=handoff['_path'], phase=handoff.get('phase'),
                                      scope='ROM upload and handoff; execution is separate')
    return dict(generated=True, source='scripts/ae350_report.py', artifact=artifact,
                payload_artifact=payload, operations=entries, acceptance=acceptance)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', required=True, type=Path)
    parser.add_argument('--payload', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/ae350-p2-acceptance.json')
    args = parser.parse_args()
    def reference(path):
        if path.is_dir():
            path = path / 'result.json'
        return json.loads(path.read_text(encoding='utf-8'))['artifact']
    summary = summarize(reference(args.build), reference(args.payload))
    args.output.write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print('wrote', args.output)


if __name__ == '__main__':
    main()
