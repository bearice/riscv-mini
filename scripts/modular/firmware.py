"""Independent HAL SDK, bootloader and BIOS builds using a generated Ninja DAG."""
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from gateware.features import Features
from gateware.memory_map import c_header
from scripts.tools.boot_image import pack_image
from scripts.modular.rtl import generate_csr
from scripts.versions import read_versions
from .storage import ROOT, canonical, digest, file_hash, read, text_file, write, workspace_lock
from .environment import discover, identity, output, process_environment
from .contracts import requirements
from .workspace import working
from .compile import BASE_FLAGS, ROLES, source_files, ninja_build

def hal_sources(v):
    f = v['features']; driver = 'firmware/drivers/'; hal = 'firmware/hal/src/'
    sources = [driver + n for n in ('uart.c', 'time.c', 'string.c')]
    sources += [hal + n for n in ('board.c', 'irq.c', 'trap.S', 'devices.c', 'disabled.c')]
    sources.append(driver + ('flash.c' if f['flash'] else 'flash_disabled.c'))
    if f['spi_lcd'] or (f['sd'] and v['sd_backend'] == 'spi'):
        sources.append(driver + 'spi.c')
    if f['spi_lcd']: sources.append(driver + 'lcd.c')
    if f['sd']: sources += [driver + 'blockcache.c', driver + ('sd_native.c' if v['sd_backend'] == 'native' else 'sd.c')]
    if f['filesystem']: sources += [driver + 'filesystem.c', 'firmware/vendor/fatfs/ff.c', 'firmware/vendor/fatfs/ffunicode.c']
    if f['video']: sources.append(driver + 'video.c')
    for flag, name in [('audio','audio.c'), ('mic','microphone.c'), ('eth','ethernet.c'), ('usb','usb.c')]:
        if f[flag]: sources.append(hal + name)
    if f['usb']:
        usb = 'firmware/vendor/tinyusb/src/'
        sources += [usb + n for n in ('tusb.c', 'common/tusb_fifo.c', 'host/usbh.c', 'host/hub.c', 'class/hid/hid_host.c')]
        sources.append(hal + 'hcd_ultra.c' if v['usb_backend'] == 'ultra' else usb + 'portable/ohci/ohci.c')
    return sources


def generated_headers(csr, v, directory):
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory.parent) as temporary:
        staging=Path(temporary)
        generate_csr(csr, staging)
        for path in staging.rglob('*'):
            if path.is_file():text_file(directory/path.relative_to(staging),path.read_text(encoding='utf-8'))
    constants = ['#pragma once']
    for key, value in sorted(csr['constants'].items()):
        if value is None: value = 1
        literal = str(value) if isinstance(value, (int, float)) else '"' + str(value).replace('"', '\\"') + '"'
        constants.append('#define ' + key.upper() + ' ' + literal)
    text_file(directory / 'generated/soc.h', '\n'.join(constants) + '\n')
    mem = ['#pragma once']
    for key, value in sorted(csr['memories'].items()):
        mem += [f'#define {key.upper()}_BASE 0x{value["base"]:08x}u', f'#define {key.upper()}_SIZE 0x{value["size"]:x}u']
    text_file(directory / 'generated/mem.h', '\n'.join(mem) + '\n')
    text_file(directory / 'memory_layout.h', c_header())
    f = Features(**v['features'])
    text_file(directory / 'hardware_features.h', f.header() + f'#define MINI_CPU_ISA "{v["isa"]}"\n'
              + f'#define MINI_SD_PROFILE "{v["sd_profile"]}"\n'
              + f'#define MINI_CPU_COMPRESSED {int(v["cpu_capabilities"]["compressed"])}\n'
              + f'#define MINI_CPU_BITMANIP {int(bool(v["cpu_capabilities"]["bitmanip"]))}\n')


def portable_flags(v):
    return ['-DMINI_CPU_DCACHE=' + str(int(v['cpu_capabilities']['dcache'])),
            '-DMINI_SD_LITE=' + str(int(v['sd_profile'] == 'lite')),
            '-march=' + v['isa'], '-mabi=' + v['abi'], *BASE_FLAGS]


def gcc_inputs(environment, v):
    gcc = environment['bindings']['target.cc'][0]
    selected = output([gcc, '-march=' + v['isa'], '-mabi=' + v['abi'], '-print-libgcc-file-name'])
    if not Path(selected).is_file():
        raise ValueError('Missing matching libgcc for ' + v['isa'] + '/' + v['abi'])
    return dict(libgcc_sha256=file_hash(selected))


def build_component(store, run, component, soc, boot=None, sdk=None, environment=None, plan=False, expected=None, force=False, fresh_workspace=True, application=None):
    sm = store.get(soc)
    v = sm['config']['validation']
    env = environment or discover(ROLES, store.root)
    versions = read_versions(store.root / 'VERSIONS.yaml')
    ver = versions['hal' if component == 'hal-sdk' else component]
    own = ['scripts/modular', 'scripts/mini.py', 'scripts/mini_ops/release.py', 'scripts/mini_ops/catalog.py', 'scripts/build_records.py', 'scripts/build_recipe.py',
           'scripts/versions.py', 'scripts/riscv_toolchain.py', 'scripts/boot_image.py', 'scripts/tools/boot_image.py',
           'gateware/csr_layout.py', 'gateware/memory_map.py', 'gateware/config.py', 'gateware/flash_xip.py', 'gateware/features.py', 'requirements.lock',
           'firmware/common', 'firmware/drivers', 'firmware/hal/include', 'firmware/boot/start.S']
    deps = {'soc-description': soc}
    if component == 'hal-sdk':
        names = hal_sources(v)
        own += ['firmware/hal/src', 'firmware/vendor/fatfs', 'firmware/vendor/tinyusb/src']
    elif component == 'bios':
        names = ['firmware/boot/start.S', *['firmware/bios/' + n for n in ('main.c', 'console.c', 'vt.c', 'settings.c', 'boot.c', 'network.c', 'benchmark.c', 'enter.S')], 'firmware/diagnostics/tests.c']
        own += ['firmware/bios', 'firmware/diagnostics', 'firmware/bootloader/app.ld']
        own += [p.relative_to(store.root).as_posix() for folder in ('firmware/vendor/fatfs','firmware/vendor/tinyusb/src')
                for p in sorted((store.root/folder).rglob('*.h'))]
        if not boot or not sdk: raise ValueError('BIOS needs bootloader and HAL SDK artifacts')
        deps.update(bootloader=boot, **{'hal-sdk': sdk})
        if application and Path(application).resolve() != store.root / 'firmware/bios/main.c':
            app = Path(application).resolve().relative_to(store.root).as_posix()
            names = ['firmware/boot/start.S', app]
            if Path(app).name == 'monitor.c': names.append('firmware/diagnostics/tests.c')
            own = [n for n in own if n not in ('firmware/bios', 'firmware/diagnostics')]
            own += names
    elif component == 'bootloader':
        names = ['firmware/boot/start.S', 'firmware/drivers/uart.c', 'firmware/drivers/time.c',
                 'firmware/bootloader/main.c', 'firmware/bootloader/ddr.c']
        names.append('firmware/bootloader/' + ('flash.c' if v['boot_mode'] == 'rom' else 'flash_xip.c') if v['features']['flash'] else 'firmware/drivers/flash_disabled.c')
        own += ['firmware/bootloader']
    else:
        raise ValueError('Unknown firmware component: ' + component)
    flags = portable_flags(v) + ['-DMINI_BOOTLOADER=' + str(int(component == 'bootloader'))]
    if component == 'bios' and 'firmware/bios/main.c' in names: flags.append('-DMINI_BIOS=1')
    if component == 'bootloader':
        flags += ['-flto', '-fstack-usage']
        if v['boot_mode'] == 'rom': flags.append('-Wl,--defsym,BOOT_ROM_SIZE=' + str(v['rom_size_bytes']))
        else:
            from gateware.flash_xip import FLASH_BASE, XIP_OFFSET, XIP_SIZE
            flags += [f'-Wl,--defsym,BOOT_XIP_ORIGIN={FLASH_BASE+XIP_OFFSET}', f'-Wl,--defsym,BOOT_XIP_SIZE={XIP_SIZE}']
    inputs = source_files(store.root, own)
    config = dict(schema=1, component=component, version=ver, sources=inputs, flags=flags,
                  rebuild_entrypoint=['scripts/mini.py','_worker','rebuild-one'],
                  translation_units=names,
                  dependencies=deps, environment=identity(env), libraries=gcc_inputs(env, v),
                  includes=['$GENERATED', 'firmware/drivers', 'firmware/bootloader', 'firmware/vendor/fatfs',
                            'firmware/hal/include', 'firmware/vendor/tinyusb/src'],
                  forced_include='$GENERATED/features.h',
                  identity_include=None if component=='hal-sdk' else {'source':'main.c','header':'$GENERATED/identity.h'},
                  linker=None if component=='hal-sdk' else 'firmware/bootloader/' + ('app.ld' if component=='bios' else 'boot.ld' if v['boot_mode']=='rom' else 'xip.ld'),
                  link_flags=['-Wl,--gc-sections', '-Wl,-Map,$OUTPUT.map', '-lgcc'],
                  compile_flags=['-MMD', '-MF', '$OBJECT.d', '-MT', '$OBJECT', '-c'],
                  reproducibility_flags=['-frandom-seed=$OBJECT_ID', '-ffile-prefix-map=$ROOT=.'],
                  archive_flags=['rcsD'], objcopy_flags=['-O', 'binary'])
    config['process_environment'] = {'LANG':'C', 'LC_ALL':'C', 'SOURCE_DATE_EPOCH':'0', 'PYTHONUTF8':'1',
                                     'PATH':'$TARGET_TOOLBIN:$SYSTEM_PATH', 'TEMP':'$WORK/tmp', 'TMP':'$WORK/tmp', 'TMPDIR':'$WORK/tmp'}
    key = digest(config)
    if expected is not None and config != expected:
        raise ValueError(component + ' inputs/flags/tools differ from release lock')
    found = store.lookup(component, key)
    if found and not force:
        run.step(component, 'REUSE', found); return found
    if plan:
        run.step(component, 'BUILD', reason='component inputs/flags/tools not in store'); return None
    with working(store, run, 'primer20k_vexriscv', component, key,
                 fresh=force and fresh_workspace) as workspace:
        work = workspace.path
        include = work / 'include'
        generated_headers(read(store.path(soc) / 'csr.json'), v, include)
        # Stable shared SDK header is independent of run ID, global HEAD, and BIOS version.
        text_file(include/'features.h','#pragma once\n#include "hardware_features.h"\n')
        header = '#pragma once\n'
        boot_data = store.get(boot) if boot else None
        boot_sha = boot_data['files'].get('outputs/boot.bin') if boot_data else None
        if component != 'hal-sdk':
            header += f'#define MINI_APP_VERSION "{ver if component == "bios" else "0.0.0"}"\n'
            header += f'#define MINI_BUILD_VERSION "{ver if component == "bootloader" else boot_data["version"]}"\n'
            header += f'#define MINI_BUILD_CONFIG "{key[:8]}"\n#define MINI_BUILD_COMMIT "{digest(inputs)[:7]}"\n#define MINI_BUILD_DIRTY 0\n'
            header += f'#define MINI_APP_ID MINI_APP_VERSION "+{key[:12]}"\n#define MINI_BUILD_ID MINI_BUILD_VERSION "+{key[:12]}"\n'
            header += f'#define MINI_BUILD_RTL_ID "rtl{sm["build_key"][:8]}"\n#define MINI_BUILD_ROM_ID "rom{(boot_sha or "pending")[:8]}"\n'
        if component!='hal-sdk':text_file(include/'identity.h',header)
        abi = sm['config']['legacy_abi_tag']
        text_file(include / 'image_abi.h', f'#define MINI_IMAGE_ABI 0x{abi:08x}u\n#define MINI_IMAGE_ABI_HEX "{abi:08x}"\n')
        dirs = [include, store.root / 'firmware/drivers', store.root / 'firmware/bootloader', store.root / 'firmware/vendor/fatfs',
                store.root / 'firmware/hal/include', store.root / 'firmware/vendor/tinyusb/src']
        compile_flags = [*flags, *[arg for d in dirs for arg in ('-I', str(d))], '-include', str(include / 'features.h')]
        libs = [store.path(sdk) / 'outputs/hal.a'] if sdk else []
        linker = None if component == 'hal-sdk' else store.root / 'firmware/bootloader' / ('app.ld' if component == 'bios' else 'boot.ld' if v['boot_mode'] == 'rom' else 'xip.ld')
        name = {'hal-sdk': 'hal', 'bios': 'app', 'bootloader': 'boot'}[component]
        headers = [p for p in include.rglob('*') if p.is_file() and p.name!='identity.h']
        ninja_build(work, [store.root / n for n in names], compile_flags, env, name, linker, libs, implicit=headers, run=run, root=store.root,
                    identity_header=include/'identity.h' if component!='hal-sdk' else None)
        if source_files(store.root, own) != inputs:
            raise ValueError('Source changed during build: ' + component)
        files = {'outputs/' + p.name: p for p in work.glob(name + '.*') if p.suffix in ('.a', '.elf', '.bin')}
        mapping = work / (name + '.map')
        if mapping.is_file():
            normalized = mapping.read_text(encoding='utf-8').replace('\\', '/')
            normalized = normalized.replace(work.as_posix(), '$WORK').replace(store.root.as_posix(), '$ROOT')
            normalized = normalized.replace(Path(env['bindings']['target.cc'][0]).parent.parent.as_posix(), '$TOOLCHAIN')
            files['outputs/' + mapping.name] = normalized.encode()
        if component == 'bios':
            image = pack_image((work / 'app.bin').read_bytes(), abi)
            files['outputs/app.img'] = image
        if component=='bootloader' and v['boot_mode']=='xip':files['outputs/xip.bin']=work/'boot.bin'
        if component == 'hal-sdk':
            files.update({'include/' + p.relative_to(include).as_posix(): p for p in include.rglob('*') if p.is_file()})
        files['build.lock.json'] = canonical(config)
        files.update({'inputs/source/' + name: store.root / name for name in inputs})
        from scripts.versions import COMPONENTS
        files['inputs/source/VERSIONS.yaml'] = ''.join(n + ': ' + (ver if n==('hal' if component=='hal-sdk' else component) else '0.0.0') + '\n' for n in COMPONENTS).encode()
        contract = requirements(sm['config']['contract'])
        ref = store.publish(component, key, files, config=dict(build=config, requires=contract,
                            image_format='mini-image-v1', image_abi_tag=abi), dependencies=deps, version=ver)
        workspace.success(ref)
    run.step(component, 'BUILD', ref)
    return ref
