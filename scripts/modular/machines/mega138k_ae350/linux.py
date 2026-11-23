"""Build the pinned AE350 UP/no-AMO kernel from repository-owned inputs."""
import shutil
import struct
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
    source = (args.source or store.build/'vendor/linux-6.18.64').resolve()
    work = location(store, run, 'mega138k_ae350', 'linux')
    output = work/'obj'
    port = store.root/'firmware/linux'
    initramfs = getattr(args, 'initramfs', None)
    if initramfs is not None:
        initramfs = initramfs.resolve()
        if not initramfs.is_file():
            raise FileNotFoundError(f'Missing --initramfs archive: {initramfs}')
        if initramfs.read_bytes()[:6] != b'070701':
            raise ValueError(f'--initramfs must be an uncompressed newc .cpio archive: {initramfs}')
        initramfs_src = str(initramfs)
    else:
        initramfs_src = str(output/'initramfs.list')
    config = (port/'ae350/linux.config').read_text().replace('@INITRAMFS_LIST@', initramfs_src)
    check_config(config)
    recipe = {str(p.relative_to(store.root)):file_hash(p) for p in
              [port/'init.c', port/'riscv_mini_ae350_fb.c', port/'riscv_mini_ae350_regs.h',
               port/'ae350_gfx3d_uapi.h', port/'ae350/linux.config', port/'ae350/source.json',
               port/'install.py', *sorted((port/'ae350/patches').glob('*.patch'))]}
    if initramfs is not None:
        recipe['initramfs'] = file_hash(initramfs)
    run.result.update(source=str(source), output_dir=str(output), recipe_sha256=recipe)
    if initramfs is not None:
        run.result['initramfs'] = str(initramfs)
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
        if initramfs is None:
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
            if initramfs is None:
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
        layout = AddressMap(resolve_machine('mega138k_ae350'))
        img_bytes = image.read_bytes()
        if len(img_bytes) < 64 or img_bytes[48:56] != b'RISCV\0\0\0':
            raise ValueError('Invalid RISC-V Linux Image built')
        footprint = struct.unpack_from('<Q', img_bytes, 16)[0]
        if footprint < len(img_bytes) or footprint > layout.OS_KERNEL_BYTES:
            raise ValueError(
                f'AE350 Linux Image footprint {footprint} (file {len(img_bytes)}) exceeds '
                f'OS_KERNEL_BYTES {layout.OS_KERNEL_BYTES}'
            )
        run.result.update(image_bytes=len(img_bytes), image_footprint_bytes=footprint)
        outputs = {'Image': image, 'config': output/'.config'}
        if initramfs is None:
            outputs['init'] = output/'mini-init'
        artifact, delivery = archive(store, run, args, workspace, outputs)
        run.finish(artifact=artifact, binary=str(delivery/'Image'), sha256=file_hash(image),
                   qualification='compiled-not-board-tested')
