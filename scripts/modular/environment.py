"""Discover tools automatically, record exact roles, and enforce locked identities."""
import importlib.metadata
import os
import platform
import shutil
import subprocess
import sys
import hashlib
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[2] / 'scripts')]
from scripts.modular.storage import ROOT, file_hash, source_file_hash, read

COMMANDS = {'cc': 'gcc', 'cxx': 'g++', 'ar': 'ar', 'make': 'make', 'dtc': 'dtc',
            'clang': 'clang', 'lld': 'ld.lld', 'objcopy': 'llvm-objcopy', 'java': 'java'}


def output(argv, **kwargs):
    return subprocess.check_output([str(a) for a in argv], text=True, stderr=subprocess.STDOUT,
                                   timeout=30, **kwargs).strip()


def discover(roles, root=ROOT):
    root = Path(root)
    local = read(root / '.tools.local.json') if (root / '.tools.local.json').is_file() else {}
    records, bindings = {}, {}
    for role in sorted(set(roles)):
        if role == 'python':
            command = [sys.executable]
            details = dict(version=platform.python_version(), binary_sha256=file_hash(sys.executable))
        elif role == 'ninja':
            path = shutil.which('ninja')
            if not path:
                path = str(Path(sys.executable).parent / ('ninja.exe' if os.name == 'nt' else 'ninja'))
            if not Path(path).is_file():
                try:
                    import ninja
                    path = str(Path(ninja.BIN_DIR) / ('ninja.exe' if os.name == 'nt' else 'ninja'))
                except ImportError:
                    raise ValueError('Missing ninja; synchronize requirements.lock')
            command = [path]
            details = dict(version=output([path, '--version']), binary_sha256=file_hash(path))
        elif role.startswith('target.'):
            gcc = local.get('gcc') or shutil.which('riscv-none-elf-gcc')
            if not gcc:
                raise ValueError('Missing target.cc; configure_tools.py or PATH must provide riscv-none-elf-gcc')
            name = role.split('.')[1]
            stem = 'gcc' if name == 'cc' else name
            path = Path(gcc).with_name('riscv-none-elf-' + stem + ('.exe' if os.name == 'nt' else ''))
            if not path.is_file():
                raise ValueError('Missing ' + role + ': ' + str(path))
            command = [str(path)]
            details = dict(version=output([path, '--version']).splitlines()[0], binary_sha256=file_hash(path))
            if role == 'target.cc':
                details['triple'] = output([path, '-dumpmachine'])
                cc1=Path(output([path,'-print-prog-name=cc1']))
                if not cc1.is_file():raise ValueError('Missing compiler cc1: '+str(cc1))
                details['cc1_sha256']=file_hash(cc1)
                details['specs_sha256']=hashlib.sha256(output([path,'-dumpspecs']).encode()).hexdigest()
                include=Path(output([path,'-print-file-name=include']))
                if not include.is_dir():raise ValueError('Missing compiler builtin headers')
                headers={p.relative_to(include).as_posix():source_file_hash(p) for p in sorted(include.rglob('*')) if p.is_file()}
                from scripts.modular.storage import digest
                details['builtin_headers_sha256']=digest(headers)
        elif role == 'gowin':
            path = local.get('gowin') or shutil.which('gw_sh')
            if not path or not Path(path).is_file():
                raise ValueError('Missing gowin; configure_tools.py or PATH must provide gw_sh')
            command = [path]
            # Licensed tool does not consistently implement --version.
            details = dict(binary_sha256=file_hash(path), version=Path(path).parents[2].name)
        elif role == 'java':
            home = os.environ.get('JAVA_HOME')
            bundled=sorted((root.parent/'tools/jdk8').glob('*/bin/java.exe')) if os.name=='nt' else []
            path = local.get('java') or (str(Path(home) / 'bin' / ('java.exe' if os.name == 'nt' else 'java')) if home else str(bundled[-1]) if bundled else shutil.which('java'))
            if not path or not Path(path).is_file():
                raise ValueError('Missing java; activate the Nix shell or configure JAVA_HOME')
            command = [path]
            details = dict(version=output([path, '-version']), binary_sha256=file_hash(path))
            if '"1.8.' not in details['version']:
                raise ValueError('CPU generator requires Java 8; found '+details['version'].splitlines()[0])
        elif role=='sbt-launch':
            candidate=os.environ.get('SBT_LAUNCH_JAR') or local.get('sbt_launch')
            matches=sorted((root.parent/'tools').glob('**/sbt-launch*.jar')) if not candidate else []
            path=Path(candidate) if candidate else matches[0] if len(matches)==1 else None
            if path is None or not path.is_file():raise ValueError('sbt launcher not uniquely found; configure SBT_LAUNCH_JAR')
            import zipfile
            with zipfile.ZipFile(path) as archive:
                manifest=archive.read('META-INF/MANIFEST.MF').decode('utf-8')
            command=[str(path)]
            details=dict(binary_sha256=file_hash(path),manifest=manifest)
        else:
            raise ValueError('Unknown tool role: ' + role)
        records[role] = details
        bindings[role] = command
    return dict(platform=dict(os=platform.system(), arch=platform.machine()), tools=records, bindings=bindings)


def identity(environment):
    return {k: environment[k] for k in ('platform', 'tools')}


def process_environment(extra=None):
    """Keep OS execution support, drop implicit compiler flags/Python overrides."""
    names = ('SystemRoot','WINDIR','COMSPEC','PATHEXT','HOME','USERPROFILE','PATH','TMP','TEMP','TMPDIR')
    result = {name: os.environ[name] for name in names if name in os.environ}
    result.update(LANG='C', LC_ALL='C', SOURCE_DATE_EPOCH='0', PYTHONUTF8='1')
    result.update(extra or {})
    return result


def check_locked(expected, actual):
    if expected != identity(actual):
        raise ValueError('Resolved tools/environment differ from lock; no automatic version substitution')


def linux_runner(required, root=ROOT):
    """Choose native Linux or a capable WSL instance; names are machine bindings."""
    names = {role: COMMANDS.get(role, role) for role in required}
    candidates = [None] if platform.system() == 'Linux' else []
    if os.name == 'nt' and not candidates:
        listing = subprocess.check_output(['wsl', '--list', '--quiet'], timeout=30)
        text = listing.decode('utf-16-le') if b'\0' in listing else listing.decode()
        candidates = sorted(x.strip() for x in text.splitlines() if x.strip())
    local = read(Path(root) / '.tools.local.json') if (Path(root) / '.tools.local.json').is_file() else {}
    preferred = local.get('wsl_distro')
    candidates.sort(key=lambda item: (item != preferred, str(item)))
    errors = []
    for distro in candidates:
        prefix = [] if distro is None else ['wsl', '-d', distro, '--']
        try:
            tools = {}
            for role, command in names.items():
                if not __import__('re').fullmatch(r'[a-zA-Z0-9_.+-]+',command):raise ValueError('Invalid tool name')
                path = output([*prefix, 'sh', '-lc', 'command -v ' + command])
                version = output([*prefix, path, '-version' if role == 'java' else '--version']).splitlines()[0]
                sha = output([*prefix, 'sha256sum', path]).split()[0]
                tools[role] = dict(version=version, binary_sha256=sha, path=path)
            return dict(kind='native' if distro is None else 'wsl', distro=distro, tools=tools, prefix=prefix)
        except (OSError, subprocess.SubprocessError) as error:
            errors.append(str(distro) + ': ' + str(error))
    raise ValueError('No Linux execution environment satisfies ' + ', '.join(required) + ': ' + '; '.join(errors))


def python_packages():
    result = {}
    for name in ('migen', 'litex', 'litex-boards', 'litedram', 'liteeth', 'litesdcard', 'litespi', 'pythondata-cpu-vexriscv'):
        package = importlib.metadata.distribution(name)
        result[name] = dict(version=package.version, source=package.read_text('direct_url.json'))
    return result
