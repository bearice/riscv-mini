"""Resolve the AE350 diagnostic payload artifact.

The payload is built on its own (`mini.py build payload --machine
mega138k_ae350 --ae350-profile ddr`) so changing it never rebuilds the ROM or the
gateware. Operations require an explicit selection and verify the complete
artifact closure, machine and address ABI before touching hardware.
"""
from pathlib import Path

from modular.storage import read, member


def payload_file(root):
    """Binary location belongs to the verified artifact, not the diagnostic op."""
    config = read(root / 'manifest.json')['config']
    return member(root, config.get('binary', 'outputs/tests.bin'))


def application_parameters(root, values):
    """Patch only the application's declared parameter block before image CRC.

    The verified artifact owns its layout. This is application configuration,
    not a host memory write or an extension of the ROM loading protocol.
    """
    import struct
    manifest = read(root / 'manifest.json')
    name = manifest['config'].get('parameters')
    if not name:
        raise ValueError('Application does not declare runtime parameters')
    layout = read(member(root, name))
    fields = layout['fields']
    if set(values) != set(fields) or layout.get('format') != '5I-le' or len(fields) != 5:
        raise ValueError('Application parameter contract mismatch')
    data = bytearray(payload_file(root).read_bytes())
    offset = layout['offset']
    if not isinstance(offset, int) or offset < 0 or offset + 20 > len(data) or layout.get('size') != 20:
        raise ValueError('Application parameter block is outside its binary')
    words = [values[field] for field in fields]
    if any(not isinstance(value, int) or not 0 <= value <= 0xffffffff for value in words):
        raise ValueError('Application parameters require unsigned 32-bit values')
    data[offset:offset+20] = struct.pack('<5I', *words)
    return bytes(data)


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
    from gateware.machines.mega138k_ae350 import address_map as ae350_map
    config = manifest['config']
    if config.get('machine') != 'mega138k_ae350' or config.get('image_abi') != ae350_map.image_abi():
        raise ValueError('Payload machine/address ABI differs from this host contract')
    if config.get('entry') != ae350_map.DDR_CODE:
        raise ValueError('Payload entry differs from the ROM loader contract')
    return ref, store.path(ref)
