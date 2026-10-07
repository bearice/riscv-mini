"""Per-component release versions (VERSIONS.yaml).

Compatibility between components is enforced by content hashes — the CSR abi_tag
embedded in every image and rtl_sha256 in validation.json — not by these numbers.
They are human-facing labels that each component bumps independently; new
components start at 0.1.0. This module is the single reader for every script."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

COMPONENTS = ('rtl', 'bootloader', 'hal', 'bios', 'opensbi', 'uboot')
SEMVER = re.compile(r'(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\Z')


def read_versions(path=ROOT / 'VERSIONS.yaml'):
    data = yaml.safe_load(Path(path).read_text(encoding='utf-8')) or {}
    if not isinstance(data, dict):
        raise ValueError('VERSIONS.yaml must map each component to a version')
    missing = [c for c in COMPONENTS if c not in data]
    if missing:
        raise ValueError('VERSIONS.yaml missing components: ' + ', '.join(missing))
    unknown = [c for c in data if c not in COMPONENTS]
    if unknown:
        raise ValueError('VERSIONS.yaml has unknown components: ' + ', '.join(unknown))
    for component, value in data.items():
        if not SEMVER.match(str(value)):
            raise ValueError(f'{component} version {value!r} is not MAJOR.MINOR.PATCH')
    return {component: str(data[component]) for component in COMPONENTS}


def component_version(component, path=ROOT / 'VERSIONS.yaml'):
    return read_versions(path)[component]


def read_releases(path=ROOT / 'RELEASES.yaml'):
    """System bundles: a board-qualified combination pinning exact component versions."""
    path = Path(path)
    data = (yaml.safe_load(path.read_text(encoding='utf-8')) if path.is_file() else None) or {}
    if not isinstance(data, dict):
        raise ValueError('RELEASES.yaml must map each bundle version to component versions')
    for bundle, pins in data.items():
        if not SEMVER.match(str(bundle)):
            raise ValueError(f'bundle version {bundle!r} is not MAJOR.MINOR.PATCH')
        if not isinstance(pins, dict) or set(pins) != set(COMPONENTS):
            raise ValueError(f'bundle {bundle} must pin exactly the six components')
        for component, value in pins.items():
            if not SEMVER.match(str(value)):
                raise ValueError(f'bundle {bundle} pins {component}={value!r}, not MAJOR.MINOR.PATCH')
    return {str(bundle): {component: str(pins[component]) for component in COMPONENTS}
            for bundle, pins in data.items()}


def bundle_versions(version, path=ROOT / 'RELEASES.yaml'):
    releases = read_releases(path)
    if version not in releases:
        raise ValueError(f'Add bundle {version} to RELEASES.yaml before releasing it')
    return releases[version]
