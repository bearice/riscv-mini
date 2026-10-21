"""Mutable build state, separate from immutable successful artifacts."""
from contextlib import contextmanager, ExitStack
from pathlib import Path
import re
import stat

import sys
sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[2] / 'scripts')]
from scripts.modular.storage import read, write, workspace_lock


def workspace_name(value):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,47}', value):
        raise ValueError('Workspace name must contain 1..48 lowercase letters, digits, _ or -')
    return value


def remove_file(path):
    # Gowin marks .fs outputs read-only on Windows.
    path.chmod(stat.S_IREAD | stat.S_IWRITE)
    path.unlink()


def remove_tree(path):
    import shutil
    def writable_retry(operation, name, error):
        Path(name).chmod(stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
        operation(name)
    shutil.rmtree(path, onerror=writable_retry)


def location(store, run, machine, target, fresh=False):
    slot = workspace_name(run.result.get('workspace', 'default'))
    if fresh:
        # Source replay deliberately verifies a cold compiler invocation.
        return store.build / 'work/replay' / run.id / machine / target
    return store.build / 'work' / machine / target / slot


class Workspace:
    def __init__(self, path, run, key, previous):
        self.path, self.run, self.key = path, run, key
        self.previous = previous.get('last_success')
        self.implementation = previous.get('last_implementation') or self.previous
        self.state = dict(schema=1, status='running', run=run.id, input_key=key,
                          last_success=self.previous, last_implementation=previous.get('last_implementation'))
        self.completed = False
        write(path / 'workspace.json', self.state)

    def success(self, artifact=None, implementation=False):
        self.state.update(status='succeeded', last_success=dict(
            run=self.run.id, input_key=self.key, artifact=artifact))
        if implementation:
            self.state['last_implementation'] = self.state['last_success']
        write(self.path / 'workspace.json', self.state)
        self.completed = True


@contextmanager
def working(store, run, machine, target, key, fresh=False, path=None):
    path = Path(path) if path is not None else location(store, run, machine, target, fresh)
    with ExitStack() as leases:
        # The maintenance lock covers registration only; independent builds
        # keep running concurrently after obtaining their own work-area lock.
        with workspace_lock(store.build / 'work/.maintenance-lock'):
            leases.enter_context(workspace_lock(path))
        state = read(path / 'workspace.json') if (path / 'workspace.json').is_file() else {}
        workspace = Workspace(path, run, key, state)
        run.result.setdefault('workspaces', []).append(dict(
            machine=machine, target=target, path=str(path), input_key=key,
            previous_success=workspace.previous))
        run.save()
        try:
            yield workspace
        finally:
            if not workspace.completed:
                workspace.state['status'] = 'failed'
                write(path / 'workspace.json', workspace.state)


def clean_workspaces(store, candidates):
    """Exclude new builds, reject active leases, then remove expendable areas."""
    import shutil
    with workspace_lock(store.build / 'work/.maintenance-lock'):
        for path in candidates:
            resolved = path.resolve()
            if not any(resolved.is_relative_to((store.build / name).resolve()) and
                       resolved != (store.build / name).resolve() for name in ('work', 'cache')):
                raise ValueError('Cleanup path escapes disposable workspace')
            for lock in path.rglob('.lock'):
                # Releasing a successful probe is safe while maintenance
                # excludes new registrations. Windows needs handles closed
                # before the directories containing them can be deleted.
                with workspace_lock(lock.parent):
                    pass
        for path in candidates:
            if path.is_dir():
                remove_tree(path)
            else:
                remove_file(path)


@contextmanager
def maintenance(store):
    """Freeze workspace registrations and reject any active build lease."""
    root = store.build / 'work'
    with workspace_lock(root / '.maintenance-lock'):
        own = {root / '.maintenance-lock/.lock', root / '.session-lock/.lock'}
        for lock in root.rglob('.lock'):
            if lock not in own:
                with workspace_lock(lock.parent):
                    pass
        yield


def clear_generated(work, folders):
    """Remove obsolete generated sources; retain vendor implementation caches."""
    import shutil
    root = Path(work).resolve()
    for name in folders:
        path = (root / name).resolve()
        if not path.is_relative_to(root) or path == root:
            raise ValueError('Generated path escapes workspace')
        if path.is_dir():
            remove_tree(path)


def invalidate_pnr(work):
    """Old final outputs must never satisfy this attempt's acceptance checks."""
    pnr = Path(work) / 'gateware/impl/pnr'
    for path in pnr.glob('*'):
        if path.is_file() and path.suffix.lower() in ('.fs', '.tr', '.txt', '.html', '.p', '.pr'):
            remove_file(path)
    for path in (Path(work) / 'gateware').glob('*.fs'):
        remove_file(path)


def gateware_files(work, synthesized):
    """Archive generated inputs, seeds and reports, excluding compiler caches."""
    root = Path(work) / 'gateware'
    for path in root.rglob('*'):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if 'impl' not in relative.parts:
            yield path
        elif synthesized and relative.parts[:2] == ('impl', 'pnr'):
            if path.suffix.lower() in ('.fs', '.p', '.pr', '.tr', '.txt', '.html', '.posp'):
                yield path


def cached_implementation(store, component, input_key):
    index = store.build / 'index/implementation-inputs' / component / (input_key + '.json')
    if index.is_file():
        ref = read(index)
        if store.get(ref)['config'].get('input_key') != input_key:
            raise ValueError('Implementation input index differs')
        return ref
    matches = []
    for path in (store.build / 'store' / component).glob('*/manifest.json'):
        data = read(path)
        if data.get('config', {}).get('input_key') == input_key:
            ref = dict(component=component, digest=data['digest'])
            store.get(ref)
            matches.append(ref)
    if len(matches) > 1:
        raise ValueError('Ambiguous implementation input; select a preserved artifact')
    if matches:
        record_implementation(store, component, input_key, matches[0])
        return matches[0]
    return None


def record_implementation(store, component, input_key, ref):
    path = store.build / 'index/implementation-inputs' / component / (input_key + '.json')
    with workspace_lock(store.build / 'work/.implementation-index-lock'):
        if path.is_file() and read(path) != ref:
            raise ValueError('Implementation input already has different preserved output')
        write(path, ref)
