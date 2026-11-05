"""Reconstruct disposable upstream trees from pinned archives and the port installers.

Shared framework layer; machine packages own their pin tables. Every vendor
source has one manifest (`source.json`: version, archive, url, archive_sha256,
installer, tree_sha256). The installer named there
(`firmware/<component>/install.py --machine <m>`) is the only step between the
pristine archive and the build tree; its machine patches hold edits to upstream
code, so a manifest lists no patches of its own.
"""
import hashlib
import json
import os
import posixpath
import shlex
import shutil
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


def _archive_metadata(members):
    """Archive metadata split into content members and link members.

    Returns ({name: 'dir'|'file'}, {link member name: target member name}).
    Link targets are resolved through the metadata, never the filesystem, so
    the result does not depend on what the extractor managed to create."""
    contents = {}
    links = {}
    for member in members:
        name = member.name
        if member.issym():
            links[name] = posixpath.normpath(posixpath.join(posixpath.dirname(name), member.linkname))
        elif member.islnk():
            links[name] = posixpath.normpath(member.linkname)
        elif member.isdir():
            contents[name] = 'dir'
        elif member.isfile():
            contents[name] = 'file'
    return contents, links


def extract(archive, staging):
    """Extract one pinned archive into staging, deterministically on any host.

    Git archives carry symlinks. Python's fallback where the process lacks the
    symlink privilege copies file links but silently leaves directory links
    empty, while a privileged process creates them as links: the same archive
    would then hash to different trees on different interpreters. The contract
    tree always holds the CONTENT each link member points at; link chains are
    followed through the archive metadata itself."""
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        tar.extractall(staging, filter='data')
    contents, links = _archive_metadata(members)

    def resolve(name):
        seen = []
        while name in links:
            if name in seen:
                raise ValueError('archive link cycle at %s' % name)
            seen.append(name)
            name = links[name]
        if name not in contents:
            raise ValueError('archive link %s points at %s, which is not in the archive' % (seen[-1], name))
        return name

    for member in members:
        if not (member.issym() or member.islnk()):
            continue
        target = resolve(member.name)
        destination = staging/member.name
        source = staging/target
        if destination.is_symlink() or destination.is_file():
            destination.unlink()
        elif destination.is_dir():
            if any(destination.iterdir()):
                raise ValueError('unexpected content where the archive places link %s' % member.name)
            destination.rmdir()
        if contents[target] == 'dir':
            shutil.copytree(source, destination, symlinks=False)
        else:
            shutil.copyfile(source, destination)


def manifest(store, path):
    path = Path(path)
    path = path if path.is_absolute() else store.root/path
    spec = json.loads(path.read_text())
    if spec.get('patches') or not spec.get('installer'):
        raise ValueError('%s: vendor patches are applied by the manifest installer, not listed in the manifest' % path)
    return path, spec


def installer(store, spec):
    """The manifest installer as a command line; the tree argument follows the script."""
    script, *options = shlex.split(spec['installer'])
    return [sys.executable, str(store.root/script)], options


def installer_inputs(store, name, manifests):
    """Repository files the installation reads, as reported by the installer itself."""
    command, options = installer(store, manifest(store, manifests[name])[1])
    listing = subprocess.run([*command, '--inputs', *options], check=True, capture_output=True, text=True).stdout
    return [Path(line) for line in listing.splitlines()]


def prepare(store, run, name, source, manifests, dry=False):
    manifest_path, spec = manifest(store, manifests[name])
    archive = store.build/'cache/downloads'/spec['archive']
    run.result['upstream_source'] = dict(manifest=str(manifest_path), **spec,
        installer_inputs={p.relative_to(store.root).as_posix():file_hash(p) for p in installer_inputs(store, name, manifests)})
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
            extract(archive, staging)
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
