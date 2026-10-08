"""Resolve and validate a complete operation before touching the board."""
import hashlib
import json
from pathlib import Path

from boot_image import FLASH_OFFSET, FLASH_SIZE, abi_tag, unpack_image
from build_records import ROOT, artifact_identity, get_record, pnr_passed


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def configuration_bytes(path):
    """Gowin FS contains binary rows and // metadata, not an opaque binary."""
    bits = 0
    for line in Path(path).read_text(encoding='ascii').splitlines():
        line = line.strip()
        if not line or line.startswith('//'):
            continue
        if set(line) - {'0', '1'}:
            raise ValueError('Unsupported FS format; cannot establish configuration extent')
        bits += len(line)
    if not bits or bits % 8:
        raise ValueError('Invalid FS bit count')
    # Include a conservative sector rounding when checking partition overlap.
    return ((bits // 8 + 4095) // 4096) * 4096


class Build:
    def __init__(self, name='current', image=None, root=ROOT):
        candidate = Path(name)
        if (candidate / 'validation.json').is_file():
            self.path = candidate.resolve()
            self.id = str(self.path)
        else:
            record = get_record(name, root=root)
            self.path = Path(record['path']).resolve()
            self.id = record['id']
        self.validation = artifact_identity(self.path)
        if not pnr_passed(self.validation):
            raise ValueError('Board operations require completed PnR with setup/hold 0/0')
        self.fs = self.path / 'gateware/riscv_mini.fs'
        self.image_path = Path(image).resolve() if image else self.path / 'firmware/app.img'
        self.image = self.image_path.read_bytes()
        self.image_sha256 = hashlib.sha256(self.image).hexdigest()
        unpack_image(self.image, self.validation['boot_image']['abi_tag'])
        self.mode = self.validation.get('boot_mode', 'rom')
        if self.mode not in ('rom', 'xip'):
            raise ValueError('Unknown boot mode: ' + self.mode)

    def describe(self):
        return dict(id=self.id, path=str(self.path), boot_mode=self.mode,
                    bitstream_sha256=self.validation['bitstream_sha256'], image_sha256=self.image_sha256,
                    abi_tag=self.validation['boot_image']['abi_tag'])

    def csr(self):
        candidates = [self.path / 'csr.json']
        # Compatibility for old release packages: trust the original run only
        # after checking BOTH artifacts and the CSR-derived application ABI.
        old = self.path / 'build-parameters.json'
        if old.is_file():
            run = Path(json.loads(old.read_text(encoding='utf-8')).get('original_run', ''))
            if run.is_absolute() and (run / 'validation.json').is_file():
                v = artifact_identity(run)
                if (v.get('bitstream_sha256') == self.validation['bitstream_sha256']
                        and v['boot_image']['sha256'] == self.validation['boot_image']['sha256']):
                    candidates.append(run / 'csr.json')
        for path in candidates:
            if path.is_file():
                csr = json.loads(path.read_text(encoding='utf-8'))
                if abi_tag(csr) != self.validation['boot_image']['abi_tag']:
                    raise ValueError('CSR metadata ABI differs from selected build')
                return csr, path
        raise ValueError('Missing csr.json; old packages require their matching original run. '
                         'New release packages include this deployment metadata.')

    def update_plan(self):
        if sha256(self.image_path)!=self.image_sha256:
            raise ValueError('Application changed during preflight')
        if not self.validation.get('features', {}).get('flash', True):
            raise ValueError('Selected build has no Flash')
        limit = FLASH_OFFSET
        writes = []
        if self.mode == 'xip':
            xip = self.validation['xip']
            offset = xip['flash_offset']
            limit = offset
            binary = self.path / 'firmware/xip.bin'
            if sha256(binary) != xip['sha256'] or binary.stat().st_size != self.validation['firmware_bytes']:
                raise ValueError('XIP binary differs from validation.json')
            if (offset <= 0 or offset % 4096 or
                    binary.stat().st_size > xip['reserved_bytes'] or
                    offset + xip['reserved_bytes'] > FLASH_OFFSET or
                    xip['reset_address'] != xip['base'] + offset):
                raise ValueError('XIP layout overlaps configuration/application or has invalid reset address')
            writes.append(dict(name='xip', offset=offset, path=str(binary), sha256=sha256(binary)))
        size = configuration_bytes(self.fs)
        if size >= limit:
            raise ValueError('Configuration image does not fit below boot/application partition')
        if FLASH_OFFSET + len(self.image) > FLASH_SIZE:
            raise ValueError('Application exceeds Flash partition')
        writes.append(dict(name='application', offset=FLASH_OFFSET, path=str(self.image_path),
                           sha256=self.image_sha256))
        return dict(configuration_bytes=size, writes=writes,
                    method='ddr-software-spi' if self.mode == 'xip' else 'uart-install',
                    preparation=('SRAM target gateware; DDR utility disables XIP, waits busy=0 and confirms SPI CS idle'
                                 if self.mode == 'xip' else 'configuration write and reload'),
                    verification='exact readback of application and XIP, then Flash boot')
