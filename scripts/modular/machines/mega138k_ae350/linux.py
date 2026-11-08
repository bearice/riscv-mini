"""Build the pinned AE350 UP/no-AMO kernel from repository-owned inputs."""
import shutil
import subprocess
from gateware.description import resolve_machine
from gateware.machines.mega138k_ae350.address_map import AddressMap
from scripts.modular.storage import file_hash, digest
from scripts.modular.workspace import working, location
from scripts.modular.machines.mega138k_ae350.upstream import archive
from scripts.modular.machines.mega138k_ae350.sourceprep import prepare


def check_config(config):
    """The kernel virtual layout is part of the AE350 OS address contract."""
    layout = AddressMap(resolve_machine('mega138k_ae350'))
    layout.require()
    values = dict(line.split('=', 1) for line in config.splitlines() if line.startswith('CONFIG_') and '=' in line)
    if int(values.get('CONFIG_PAGE_OFFSET', '0'), 0) != layout.OS_PAGE_OFFSET:
        raise ValueError('CONFIG_PAGE_OFFSET differs from the AE350 address contract')
    reserved = [int(values.get('CONFIG_RISCV_RESERVED_VA_' + key, '0'), 0) for key in ('START', 'SIZE')]
    if reserved != [layout.OS_RESERVED_VA_BASE, layout.OS_RESERVED_VA_BYTES]:
        raise ValueError('CONFIG_RISCV_RESERVED_VA_START/SIZE differ from the AE350 reserved kernel VA')
    if values.get('CONFIG_32BIT') != 'y' or values.get('CONFIG_MMU') != 'y':
        raise ValueError('AE350 OS address contract assumes an Sv32 MMU kernel')


def build(store, run, args, dry):
    source = (args.source or store.build/'vendor/linux-6.12.112').resolve()
    work = location(store, run, 'mega138k_ae350', 'linux')
    output = work/'obj'
    port = store.root/'firmware/linux'
    config = (port/'ae350/linux.config').read_text().replace('@INITRAMFS_LIST@', str(output/'initramfs.list'))
    check_config(config)
    recipe = {str(p.relative_to(store.root)):file_hash(p) for p in
              [port/'init.c', port/'ae350/linux.config', port/'ae350/source.json', port/'install.py',
               *sorted((port/'ae350/patches').glob('*.patch'))]}
    run.result.update(source=str(source), output_dir=str(output), recipe_sha256=recipe)
    if dry:
        prepare(store, run, 'linux', source, dry=True)
        run.finish(plan=True)
        return
    with working(store, run, 'mega138k_ae350', 'linux', digest(recipe)) as workspace:
        prepare(store, run, 'linux', source)
        output.mkdir(parents=True, exist_ok=True)
        config_path = output/'.config'
        if not config_path.exists() or config_path.read_text() != config:
            config_path.write_text(config)
        listing = ('dir /dev 755 0 0\nnod /dev/console 600 0 0 c 5 1\n'
                   'nod /dev/fb0 600 0 0 c 29 0\ndir /proc 755 0 0\ndir /sys 755 0 0\n'
                   'dir /mnt 755 0 0\nfile /init '+str(output/'mini-init')+' 755 0 0\n')
        if not (output/'initramfs.list').exists() or (output/'initramfs.list').read_text() != listing:
            (output/'initramfs.list').write_text(listing)
        with (run.path/'build.log').open('w') as log:
            if not (output/'uapi/include/linux/types.h').exists():
                subprocess.run(['make','-C',str(source),'ARCH=riscv','O='+str(output),'headers'],
                               check=True,stdout=log,stderr=log)
                shutil.copytree(output/'usr/include',output/'uapi/include',dirs_exist_ok=True)
            subprocess.run(['clang', '--target=riscv32-unknown-linux-gnu',
                            '-march=rv32im_zicsr_zifencei', '-mabi=ilp32', '-Os',
                            '-Wall', '-Wextra', '-Werror', '-fno-builtin', '-fno-stack-protector',
                            '-fno-pic', '-ffreestanding', '-nostdlib', '-static', '-fuse-ld=lld',
                            '-DMINI_LINUX_HEADLESS', '-DMINI_LINUX_FPU', '-I', str(port/'uapi-shim'),
                            '-isystem', str(output/'uapi/include'), '-Wl,-e,_start',
                            '-Wl,--build-id=none', str(port/'init.c'), '-o', str(output/'mini-init')],
                           check=True, stdout=log, stderr=log)
            subprocess.run(['make', '-C', str(source), 'O='+str(output), 'ARCH=riscv', 'LLVM=1', 'olddefconfig'],
                           check=True, stdout=log, stderr=log)
            subprocess.run(['make', '-C', str(source), 'O='+str(output), 'ARCH=riscv', 'LLVM=1', '-j8', 'Image'],
                           check=True, stdout=log, stderr=log)
        image = output/'arch/riscv/boot/Image'
        artifact, delivery = archive(store, run, args, workspace,
                                     {'Image':image, 'init':output/'mini-init', 'config':output/'.config'})
        run.finish(artifact=artifact, binary=str(delivery/'Image'), sha256=file_hash(image),
                   qualification='compiled-not-board-tested')
