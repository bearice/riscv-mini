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
    """System bundles: a board-qualified combination pinning exact component versions.

    A bundle pins all six component versions, but the release package only builds
    and board-checks part of that set. `external` names the components that are
    declared compatible but NOT built or verified by this package (they are built
    and verified by their own flows, e.g. uboot_verify.py). A bundle written as a
    flat map of six versions has an empty external set."""
    path = Path(path)
    data = (yaml.safe_load(path.read_text(encoding='utf-8')) if path.is_file() else None) or {}
    if not isinstance(data, dict):
        raise ValueError('RELEASES.yaml must map each bundle version to component versions')
    releases = {}
    for bundle, spec in data.items():
        if not SEMVER.match(str(bundle)):
            raise ValueError(f'bundle version {bundle!r} is not MAJOR.MINOR.PATCH')
        if isinstance(spec, dict) and 'components' in spec:
            pins, external = spec['components'], spec.get('external', [])
        else:
            pins, external = spec, []
        if not isinstance(pins, dict) or set(pins) != set(COMPONENTS):
            raise ValueError(f'bundle {bundle} must pin exactly the six components')
        for component, value in pins.items():
            if not SEMVER.match(str(value)):
                raise ValueError(f'bundle {bundle} pins {component}={value!r}, not MAJOR.MINOR.PATCH')
        if not isinstance(external, list) or any(e not in COMPONENTS for e in external):
            raise ValueError(f'bundle {bundle} external must list component names')
        releases[str(bundle)] = {'components': {c: str(pins[c]) for c in COMPONENTS},
                                'external': [str(e) for e in external]}
    return releases


def bundle_versions(version, path=ROOT / 'RELEASES.yaml'):
    releases = read_releases(path)
    if version not in releases:
        raise ValueError(f'Add bundle {version} to RELEASES.yaml before releasing it')
    return releases[version]['components']


def bundle_external(version, path=ROOT / 'RELEASES.yaml'):
    """Components declared compatible by the bundle but not built/verified by its package."""
    releases = read_releases(path)
    if version not in releases:
        raise ValueError(f'Add bundle {version} to RELEASES.yaml before releasing it')
    return releases[version]['external']
