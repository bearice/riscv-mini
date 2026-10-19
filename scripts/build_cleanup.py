"""Collect obsolete outputs while retaining final references and artifact closures."""
import json
import re
import shutil
import subprocess
from pathlib import Path
from scripts.modular.storage import write

COMPONENT = re.compile(r"[0-9a-f]{64}")
RUN_ID = re.compile(r"20[0-9]{6}T[0-9]+Z-[A-Za-z0-9_.-]+")
OUTPUTS = {'bios', 'bios-libc-test', 'bios-payload', 'gwbasic', 'gwbasic-sim-test',
           'lcd-csr', 'lcd-pwm', 'lcd-refresh-research', 'libc-board', 'libc-test-app',
           'packed-window-feasibility', 'repro', 'tests', 'video-address-rtl', '__pycache__'}
INPUTS = {'doom-assets', 'doom-src'}


def references(value):
    found = set()
    if isinstance(value, dict):
        if isinstance(value.get('component'), str) and COMPONENT.fullmatch(str(value.get('digest', ''))):
            found.add((value['component'], value['digest']))
        for child in value.values():
            found.update(references(child))
    elif isinstance(value, list):
        for child in value:
            found.update(references(child))
    return found


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return None


def plan(root, active_run=None):
    root = Path(root).resolve()
    build = root / 'build'
    roots, run_ids, protected_paths = set(), set(), set()
    candidates = []
    if (root / '.git').exists():
        result = subprocess.run(['git', 'worktree', 'list', '--porcelain'], cwd=root,
                                capture_output=True, text=True, check=True)
        protected_paths.update(Path(line[9:]).resolve() for line in result.stdout.splitlines()
                               if line.startswith('worktree '))
    def note(path):
        value = read_json(path)
        if value is not None:
            roots.update(references(value))
            run_ids.update(RUN_ID.findall(json.dumps(value)))
        return value
    # Immutable archive/release manifests and pinned refs are roots. Preserve
    # original-platform candidates; AE350 provisional candidates are collectible.
    for directory in ('archives', 'releases'):
        for path in (build / directory).rglob('*.json'):
            note(path)
    for path in (build / 'refs').rglob('*.json'):
        if 'candidates' not in path.parts or path.parent == build / 'refs/candidates':
            value = note(path)
            if isinstance(value, dict) and value.get('path'):
                protected_paths.add(Path(value['path']).resolve())
        else:
            candidates.append(path)
    for path in (build / 'catalog').glob('*.json'):
        value = read_json(path)
        if value and value.get('status') != 'cleaned' and value.get('path'):
            protected_paths.add(Path(value['path']).resolve())
    # Published performance and maintained platform docs identify raw evidence.
    documents = [*(root / 'reports/performance').glob('*'),
                 *(root / 'docs').glob('*ae350*.md'), root / 'reports/ae350-p2.md',
                 root / 'reports/ae350-p2.json']
    for path in documents:
        if path.is_file() and path.suffix in ('.md', '.json'):
            run_ids.update(RUN_ID.findall(path.read_text(encoding='utf-8')))
    keep = set()
    def visit(ref):
        if ref in keep:
            return
        if not re.fullmatch(r'[A-Za-z0-9_-]+', ref[0]) or not COMPONENT.fullmatch(ref[1]):
            raise ValueError('Invalid artifact reference')
        path = build / 'store' / ref[0] / ref[1] / 'manifest.json'
        value = read_json(path)
        keep.add(ref)
        if value is None:
            if path.parent.exists():
                raise ValueError('Unreadable protected artifact manifest: ' + str(path))
            return
        for dep in value.get('dependencies', {}).values():
            visit((dep['component'], dep['digest']))
    for ref in roots:
        visit(ref)
    targets = []
    def add(path, category):
        path = path.resolve()
        if not path.is_relative_to(build.resolve()) or path == build.resolve():
            raise ValueError('Cleanup target escapes build')
        if any(p == path or p.is_relative_to(path) for p in protected_paths):
            return
        targets.append(dict(path=str(path), category=category))
    for path in build.iterdir():
        if path.is_symlink():
            continue
        if path.is_dir() and path.name not in INPUTS:
            if path.name in OUTPUTS or path.name.startswith(('doom-', 'cpu-')):
                if not (path / '.git').exists():
                    add(path, 'legacy-output')
        elif path.is_file() and path.name not in ('README.md', 'environment.json'):
            if path.suffix in ('.log', '.txt', '.png', '.raw', '.v', '.syms', '.exe', '.py', '.json', '.md') and not path.name.startswith('release-notes-'):
                add(path, 'loose-log')
    for component in (build / 'store').glob('*'):
        if component.is_dir():
            for path in component.iterdir():
                if path.is_dir() and COMPONENT.fullmatch(path.name) and (component.name, path.name) not in keep:
                    add(path, 'unreferenced-component')
    for path in candidates:
        value = read_json(path)
        if value is not None and references(value) & keep:
            run_ids.update(RUN_ID.findall(json.dumps(value)))
        elif value is not None:
            add(path, 'obsolete-candidate-ref')
    for path in (build / 'index').rglob('*.json'):
        value = read_json(path)
        if value is not None and references(value) - keep:
            add(path, 'stale-index')
    for directory in ('runs', 'operations'):
        for path in (build / directory).glob('*'):
            if not path.is_dir() or path.name in run_ids or (active_run and path.resolve() == Path(active_run).resolve()):
                continue
            value = read_json(path / 'result.json')
            if value is not None and (references(value) & keep):
                continue
            # Only typed operation/build records are disposable here. Unknown
            # legacy folders remain outside this collector's contract.
            if value is not None:
                add(path, 'obsolete-' + directory)
    return dict(scope='obsolete-build-outputs', targets=targets,
                protected_components=[dict(component=c, digest=d) for c,d in sorted(keep)],
                protected_paths=sorted(str(p) for p in protected_paths),
                preserved=['archives', 'releases', 'recipes', 'refs/current', 'refs/baseline',
                           'vendor', 'doom-assets', 'doom-src', 'linux', 'uboot', 'opensbi'])


def clean(root, execute=False):
    root = Path(root).resolve()
    # Use the same lock as modular builds, so the graph cannot change mid-GC.
    from scripts.modular.storage import workspace_lock
    with workspace_lock(root / 'build/work/.session-lock'):
        selected = plan(root)
        output = root / 'build/reports/cleanup/obsolete-plan.json'
        write(output, selected)
        counts = {}
        for item in selected['targets']:
            counts[item['category']] = counts.get(item['category'], 0) + 1
        print(('Cleaning' if execute else 'Would clean'), counts, 'plan:', output)
        if not execute:
            return selected
        deleted, retained = [], []
        for item in selected['targets']:
            path = Path(item['path'])
            if not path.resolve().is_relative_to(root / 'build') or path.resolve() == root / 'build':
                raise ValueError('Unsafe cleanup target')
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                deleted.append(item)
            except OSError as error:
                retained.append(dict(item, error=str(error)))
        write(output.with_name('obsolete-result.json'), dict(selected, deleted=deleted,
              retained=retained, passed=not retained))
        print('Deleted:',len(deleted),'retained:',len(retained))
        if retained:
            raise RuntimeError('Cleanup incomplete; see ' + str(output.with_name('obsolete-result.json')))
        return selected
