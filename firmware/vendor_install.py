"""Shared entry of the vendor-source installers (firmware/<component>/install.py).

An installer turns a pristine upstream tree into the build tree of one machine:
it copies tracked files, adds their wiring from Python tables, then applies the
machine's patches, which hold edits to upstream code only (one purpose per file,
file-name order). Installing twice leaves the tree unchanged. `--inputs` lists
every repository file the installation reads, without touching a tree.
"""
import argparse
import subprocess
from pathlib import Path


def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes().replace(b'\r\n', b'\n'))


def patches(folder):
    return sorted(folder.glob('*.patch')) if folder else []


def apply_patches(src, folder):
    """A patch may build on an earlier one, so an installed series is first unwound from
    its end and the whole series is then applied in order. On a conflict the tree is
    restored to its state before the call: no partial installation."""
    def patch(name, *options):
        # --forward stops --batch from silently flipping a patch that looks reversed.
        return subprocess.run(['patch', '-p1', '--batch', '--forward', '--fuzz=0', *options, '-i', str(name)],
                              cwd=src, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    series = patches(folder)
    unwound = [name for name in reversed(series)
               if patch(name, '--dry-run', '--reverse') and patch(name, '--reverse')]
    applied = []
    for name in series:
        if patch(name, '--dry-run') and patch(name):
            applied.append(name)
            continue
        for done in reversed(applied):
            patch(done, '--reverse')
        for done in reversed(unwound):
            patch(done)
        raise RuntimeError(f'{name.name} conflicts with the upstream tree; refusing partial installation')


def main(doc, machines, install, inputs, default=None):
    parser = argparse.ArgumentParser(description=doc)
    parser.add_argument('source', type=Path, nargs='?')
    parser.add_argument('--machine', choices=sorted(machines), default=default, required=default is None)
    parser.add_argument('--inputs', action='store_true', help='list the repository files this installation reads')
    args = parser.parse_args()
    if args.inputs:
        files = {Path(__file__).resolve(), *(Path(p).resolve() for p in inputs(args.machine))}
        print('\n'.join(str(p) for p in sorted(files) if p.is_file()))
        return
    if args.source is None:
        parser.error('the upstream source tree is required')
    install(args.source, args.machine)
