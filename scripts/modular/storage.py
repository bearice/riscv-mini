"""Immutable artifacts, rebuildable indexes, and typed operation runs."""
import hashlib
import codecs
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 1
SOURCE_HASH_FORMAT = 'sha256-text-eol-lf-v1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def normalize_text_eol(data):
    """Canonical LF line endings, preserving all other bytes and final newlines."""
    return data.replace(b'\r\n', b'\n').replace(b'\r', b'\n')


def text_file_hash(path):
    """Compatibility name for the shared build-input normalization policy."""
    return source_file_hash(path)


def normalize_source_bytes(data):
    """Recognize text by content, never by filename; keep opaque inputs exact."""
    for bom, encoding in ((codecs.BOM_UTF32_LE, 'utf-32-le'), (codecs.BOM_UTF32_BE, 'utf-32-be'),
                          (codecs.BOM_UTF16_LE, 'utf-16-le'), (codecs.BOM_UTF16_BE, 'utf-16-be')):
        if data.startswith(bom):
            try:
                text = data[len(bom):].decode(encoding)
            except UnicodeError:
                return data
            if any(ord(c) < 32 and c not in '\t\n\r\v\f' for c in text):
                return data
            return bom + text.replace('\r\n', '\n').replace('\r', '\n').encode(encoding)
    try:
        text = data.decode('utf-8')
    except UnicodeError:
        return data
    if any(ord(c) < 32 and c not in '\t\n\r\v\f' for c in text):
        return data
    return normalize_text_eol(data)


def source_bytes(path):
    return normalize_source_bytes(Path(path).read_bytes())


def source_file_hash(path):
    """Build-input identity: canonical text EOL, exact binary bytes."""
    return hashlib.sha256(source_bytes(path)).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _windows_retry(operation, *args):
    """Bound transient sharing denials while retaining atomic filesystem moves."""
    for attempt in range(6):
        try:
            return operation(*args)
        except PermissionError as error:
            if os.name != 'nt' or getattr(error, 'winerror', None) not in (5, 32) or attempt == 5:
                raise
            time.sleep(0.05 * 2 ** attempt)


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, indent=2, ensure_ascii=False) + '\n'
    if path.is_file() and path.read_text(encoding='utf-8') == data:
        return
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(data, encoding='utf-8')
    try:
        _windows_retry(os.replace, temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def text_file(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode('utf-8')
    if not path.is_file() or path.read_bytes() != data:
        path.write_bytes(data)


def member(root, name):
    root = Path(root).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('Path escapes artifact: ' + str(name))
    return path


def component_name(name):
    if not re.fullmatch(r'[a-z][a-z0-9-]*(?:/[a-z][a-z0-9-]*)?', name):
        raise ValueError('Invalid component name: ' + name)
    return name


@contextmanager
def workspace_lock(path):
    """OS lock: released on crash, no stale lock-file deletion races."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    with (path / '.lock').open('a+b') as stream:
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                if os.fstat(stream.fileno()).st_size == 0:
                    stream.write(b'0'); stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise ValueError('Workspace already in use: ' + str(path)) from error
        else:
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise ValueError('Workspace already in use: ' + str(path)) from error
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class Store:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.build = self.root / 'build'

    def path(self, ref):
        name = component_name(ref['component'])
        if not re.fullmatch('[0-9a-f]{64}', ref['digest']):
            raise ValueError('Invalid artifact digest')
        return self.build / 'store' / name / ref['digest']

    def get(self, ref, recursive=False):
        path = self.path(ref)
        data = read(path / 'manifest.json')
        core = {k: v for k, v in data.items() if k != 'digest'}
        if data.get('schema') != SCHEMA or digest(core) != ref['digest'] or data['digest'] != ref['digest'] or data['component'] != ref['component']:
            raise ValueError('Artifact manifest differs: ' + str(path))
        for name, sha in data['files'].items():
            if file_hash(member(path, name)) != sha:
                raise ValueError('Artifact file differs: ' + str(path / name))
        actual = {p.relative_to(path).as_posix() for p in path.rglob('*') if p.is_file() and p.relative_to(path).as_posix() != 'manifest.json'}
        if actual != set(data['files']):
            raise ValueError('Artifact file inventory differs: ' + str(path))
        if recursive:
            for dep in data['dependencies'].values():
                self.get(dep, recursive=True)
        return data

    def publish(self, component, key, files, config=None, dependencies=None, version=None):
        component_name(component)
        dependencies = dependencies or {}
        for dep in dependencies.values():
            self.get(dep)
        staging = self.build / 'work' / '.publish'
        staging.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(dir=staging))
        try:
            hashes = {}
            for name, source in sorted(files.items()):
                if name == 'manifest.json':
                    raise ValueError('Reserved artifact member')
                target = member(temporary, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(source, bytes):
                    target.write_bytes(source)
                else:
                    shutil.copyfile(source, target)
                hashes[name] = file_hash(target)
            core = dict(schema=SCHEMA, component=component, build_key=key,
                        version=version, config=config or {}, dependencies=dependencies, files=hashes)
            sha = digest(core)
            ref = dict(component=component, digest=sha)
            write(temporary / 'manifest.json', dict(core, digest=sha))
            destination = self.path(ref)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with workspace_lock(self.build / 'work' / '.store-lock'):
                if destination.exists():
                    self.get(ref)
                else:
                    # Windows can briefly deny renaming a newly hashed tree.
                    # Keep the atomic rename and store lock; never copy over a
                    # destination or publish an index before rename succeeds.
                    _windows_retry(temporary.rename, destination)
                index = self.build / 'index/build-keys' / component / (key + '.json')
                if index.is_file() and read(index) != ref:
                    raise ValueError('Same build key produced different artifacts; preserved conflict: ' + str(destination))
                write(index, ref)
            return ref
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def lookup(self, component, key):
        index = self.build / 'index/build-keys' / component_name(component) / (key + '.json')
        if index.is_file():
            ref = read(index)
            data = self.get(ref)
            if data['component'] != component or data['build_key'] != key:
                raise ValueError('Build-key index differs')
            return ref
        # Index is expendable; reconstruct from authoritative manifests.
        matches=[]
        for manifest in sorted((self.build / 'store' / component).glob('*/manifest.json')):
            data = read(manifest)
            if data['build_key'] == key:
                ref = dict(component=component, digest=data['digest'])
                self.get(ref); matches.append(ref)
        if len(matches)>1:raise ValueError('Ambiguous build key: multiple preserved outputs; select an exact artifact')
        if matches:
            write(index,matches[0]);return matches[0]
        return None

    def closure(self, ref):
        found = {}
        def visit(item):
            identity = item['component'] + '@' + item['digest']
            if identity in found:
                return
            found[identity] = item
            for dep in self.get(item)['dependencies'].values():
                visit(dep)
        visit(ref)
        return list(found.values())


class Run:
    def __init__(self, kind, operation, request=None, root=ROOT):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        self.id = stamp + '-' + operation.replace('/', '-') + '-' + uuid.uuid4().hex[:6]
        self.path = Path(root) / 'build/runs' / self.id
        self.path.mkdir(parents=True)
        write(self.path / 'request.json', dict(schema=SCHEMA, kind=kind, operation=operation, request=request or {}))
        self.result = dict(schema=SCHEMA, kind=kind, operation=operation, passed=False, steps=[])
        self.save()

    def step(self, component, status, ref=None, reason=None):
        self.result['steps'].append(dict(component=component, status=status, artifact=ref, reason=reason))
        print(component, status, ref['digest'][:12] if ref else '', reason or '', flush=True)
        self.save()

    def save(self):
        write(self.path / 'result.json', self.result)

    def finish(self, **values):
        self.result.update(passed=True, **values); self.save()
