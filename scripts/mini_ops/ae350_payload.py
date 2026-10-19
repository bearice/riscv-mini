"""Resolve the AE350 diagnostic payload artifact.

The payload is built on its own (`mini.py build payload --machine
mega138k_ae350 --ae350-profile p2`) so changing it never rebuilds the ROM or the
gateware. Operations require an explicit selection and verify the complete
artifact closure, machine and address ABI before touching hardware.
"""
from pathlib import Path

from modular.storage import read, member


def payload_file(root):
    """Binary location belongs to the verified artifact, not the diagnostic op."""
    config = read(root / 'manifest.json')['config']
    return member(root, config.get('binary', 'outputs/tests.bin'))


def resolve_payload(store, selection=None):
    if selection:
        path = Path(selection)
        if path.is_dir():
            path = path / 'result.json'
        ref = read(path)['artifact']
    else:
        raise ValueError('Select a payload explicitly with --payload; artifacts from unrelated runs are never selected implicitly')
    if ref.get('component') != 'payload':
        raise ValueError('Selected artifact is not an AE350 payload: %r' % (ref,))
    manifest = store.get(ref, recursive=True)
    from gateware.machines import ae350_map
    config = manifest['config']
    if config.get('machine') != 'mega138k_ae350' or config.get('image_abi') != ae350_map.image_abi():
        raise ValueError('Payload machine/address ABI differs from this host contract')
    if config.get('entry') != ae350_map.DDR_CODE:
        raise ValueError('Payload entry differs from the ROM loader contract')
    return ref, store.path(ref)
