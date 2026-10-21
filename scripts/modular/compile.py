"""Shared compiler/Ninja DAG helpers used by every machine firmware backend.

Machine-neutral: flags, source collection and the Ninja action writer contain
no platform semantics.  Machine-specific generated headers (which call
``generate_csr`` and the 20K gateware feature model) stay in the platform
backend.
"""
import os
import shlex
import subprocess
import sys
from pathlib import Path

from .storage import ROOT, canonical, digest, file_hash, text_file
from .environment import process_environment

BASE_FLAGS = ['-Os', '-Wall', '-Wextra', '-Werror', '-ffreestanding', '-fno-builtin',
              '-ffunction-sections', '-fdata-sections', '-nostdlib', '-nostartfiles', '-msmall-data-limit=0']
ROLES = ['target.cc', 'target.ar', 'target.as', 'target.ld', 'target.objcopy', 'target.size', 'target.readelf', 'ninja', 'python']


def source_files(root, names):
    files = {}
    for name in names:
        path = root / name
        if path.is_dir():
            for child in sorted(path.rglob('*')):
                if child.is_file() and child.suffix in ('.c', '.h', '.S', '.ld', '.py'):
                    files[child.relative_to(root).as_posix()] = file_hash(child)
        elif path.is_file():
            files[name] = file_hash(path)
        else:
            raise ValueError('Missing component input: ' + str(path))
    return files


def quote_command(parts):
    return subprocess.list2cmdline([str(x) for x in parts]) if os.name == 'nt' else shlex.join([str(x) for x in parts])


def ninja_path(path):
    return str(path).replace('\\', '/').replace('$', '$$').replace(' ', '$ ').replace(':', '$:')


def ninja_build(work, sources, flags, env, name, linker=None, libraries=None, implicit=(), run=None, root=ROOT, identity_header=None, identity_sources=None):
    """Commands are structured action JSONs, avoiding cross-shell quoting of argv."""
    actions = work / 'actions'; actions.mkdir(exist_ok=True)
    worker = root / 'scripts/mini.py'
    lines = ['ninja_required_version = 1.10', 'rule action',
             '  command = ' + quote_command([sys.executable, worker, '_worker', 'action']) + ' $action',
             '  description = $label', '  restat = 1',
             'rule cc', '  command = ' + quote_command([sys.executable, worker, '_worker', 'action']) + ' $action',
             '  depfile = $out.d', '  deps = gcc', '  description = CC $out', '  restat = 1']
    objects = []
    def action(label, command, out, inputs, rule='action', extra=()):
        action_path = actions / (label.replace('/', '_').replace(' ', '_') + '.json')
        record = dict(argv=[str(x) for x in command], cwd=str(root), environment=env.get('process_environment', {}))
        if label.startswith('AR-'):
            # ar rcs retains members removed from the source list unless the
            # old archive is removed first. Object files remain incremental.
            record['remove_before'] = [str(out)]
        text_file(action_path, canonical(record).decode() + '\n')
        prereq = [*inputs, action_path, worker, Path(__file__), *implicit, *extra]
        lines.append('build ' + ninja_path(out) + ': ' + rule + ' ' + ' '.join(ninja_path(p) for p in prereq))
        lines.append('  action = ' + quote_command([str(action_path)]).replace('$', '$$'))
        lines.append('  label = ' + label)
    for source in sources:
        source = Path(source)
        obj = work / 'obj' / (digest(source.relative_to(root).as_posix())[:16] + '.o')
        obj.parent.mkdir(exist_ok=True)
        uses_identity = identity_header is not None and (source.name == 'main.c' if identity_sources is None
                                                        else source in identity_sources)
        extra_flags=['-include',str(identity_header)] if uses_identity else []
        command = [*env['bindings']['target.cc'], *flags, *extra_flags, '-frandom-seed=' + obj.stem,
                   '-ffile-prefix-map=' + str(root) + '=.', '-MMD', '-MF', str(obj) + '.d', '-MT', obj, '-c', source, '-o', obj]
        action(obj.stem, command, obj, [source], rule='cc',extra=[identity_header] if uses_identity else [])
        objects.append(obj)
    target = work / (name + ('.elf' if linker else '.a'))
    if linker:
        command = [*env['bindings']['target.cc'], *flags, *objects, '-T', linker, '-Wl,--gc-sections',
                   '-Wl,-Map,' + str(work / (name + '.map')), *(libraries or []), '-lgcc', '-o', target]
        action('LINK-' + name, command, target, [*objects, linker, *(libraries or [])])
        binary = work / (name + '.bin')
        action('OBJCOPY-' + name, [*env['bindings']['target.objcopy'], '-O', 'binary', target, binary], binary, [target])
        lines.append('default ' + ninja_path(binary))
    else:
        action('AR-' + name, [*env['bindings']['target.ar'], 'rcsD', target, *objects], target, objects)
        lines.append('default ' + ninja_path(target))
    text_file(work / 'build.ninja', '\n'.join(lines) + '\n')
    # Tool directories must be available to compiler subprocesses (cc1/as/ld).
    tools = str(Path(env['bindings']['target.cc'][0]).parent)
    scratch = work / 'tmp'; scratch.mkdir(exist_ok=True)
    process = process_environment(dict(PATH=tools + os.pathsep + os.environ.get('PATH', ''),
                   TMP=str(scratch), TEMP=str(scratch), TMPDIR=str(scratch)))
    command = [*env['bindings']['ninja'], '-C', str(work), '-j', str(min(os.cpu_count() or 1, 8))]
    print('Ninja:', ' '.join(command), flush=True)
    log = (run.path if run else work) / (name + '-ninja.log')
    with log.open('w', encoding='utf-8') as stream:
        proc = subprocess.Popen(command, cwd=root, env=process, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            print(line, end='', flush=True); stream.write(line); stream.flush()
        if proc.wait():
            raise RuntimeError('Ninja build failed; see ' + str(log))
    return target
