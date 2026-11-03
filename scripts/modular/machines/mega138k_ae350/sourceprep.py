"""Reconstruct disposable upstream trees from pinned archives and the port installers.

Every vendor source has one manifest (`source.json`: version, archive, url,
archive_sha256, installer, tree_sha256). The installer named there
(`firmware/<component>/install.py --machine <m>`) is the only step between the
pristine archive and the build tree; its machine patches hold edits to upstream
code, so a manifest lists no patches of its own.
"""
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from scripts.modular.storage import file_hash


def tree_hash(root):
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if '.git' in relative.parts or relative.as_posix() == '.mini-source.json':
            continue
        if path.is_symlink():
            value = 'link:'+os.path.normpath(os.readlink(path))
        elif path.is_file():
            value = file_hash(path)
        else:
            continue
        digest.update((relative.as_posix()+'\0'+value+'\n').encode())
    return digest.hexdigest()


MANIFESTS = {'linux': 'firmware/linux/ae350/source.json',
             'opensbi': 'firmware/opensbi/ae350/source.json',
             'uboot': 'firmware/uboot/ae350/source.json'}


def manifest(store, name):
    path = store.root/MANIFESTS[name]
    spec = json.loads(path.read_text())
    if spec.get('patches') or not spec.get('installer'):
        raise ValueError('%s: vendor patches are applied by the manifest installer, not listed in the manifest' % path)
    return path, spec


def installer(store, spec):
    """The manifest installer as a command line; the tree argument follows the script."""
    script, *options = shlex.split(spec['installer'])
    return [sys.executable, str(store.root/script)], options


def installer_inputs(store, name):
    """Repository files the installation reads, as reported by the installer itself."""
    command, options = installer(store, manifest(store, name)[1])
    listing = subprocess.run([*command, '--inputs', *options], check=True, capture_output=True, text=True).stdout
    return [Path(line) for line in listing.splitlines()]


def prepare(store, run, name, source, dry=False):
    manifest_path, spec = manifest(store, name)
    archive = store.build/'cache/downloads'/spec['archive']
    run.result['upstream_source'] = dict(manifest=str(manifest_path), **spec,
        installer_inputs={str(p.relative_to(store.root)):file_hash(p) for p in installer_inputs(store, name)})
    if dry:
        return source
    if not source.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.exists():
            subprocess.run(['curl','-fL','--retry','2','-o',str(archive),spec['url']], check=True)
        if file_hash(archive) != spec['archive_sha256']:
            raise ValueError('Upstream archive checksum mismatch: '+str(archive))
        source.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.prepare-', dir=source.parent) as temp:
            staging = Path(temp)
            with tarfile.open(archive) as tar:
                tar.extractall(staging, filter='data')
            children = list(staging.iterdir())
            if len(children) != 1 or not children[0].is_dir():
                raise ValueError('Expected one upstream archive root')
            tree = children[0]
            command, options = installer(store, spec)
            subprocess.run([*command, str(tree), *options], check=True)
            if tree_hash(tree) != spec['tree_sha256']:
                raise ValueError('Reconstructed source differs from the saved source tree')
            tree.rename(source)
    actual = tree_hash(source)
    if actual != spec['tree_sha256']:
        raise ValueError('Unrecorded upstream edits: save/update the port installer inputs and source manifest before building '+name)
    return source
