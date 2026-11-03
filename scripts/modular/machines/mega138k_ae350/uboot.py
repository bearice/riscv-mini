"""Build the AE350 OpenSBI FW_PAYLOAD with U-Boot through the common build entry."""
import hashlib
import json
import subprocess
from pathlib import Path

from gateware.description import resolve_machine
from gateware.machines.mega138k_ae350.address_map import AddressMap
from scripts.modular.storage import file_hash
from scripts.modular.workspace import working, location
from scripts.modular.machines.mega138k_ae350.upstream import archive
from scripts.modular.machines.mega138k_ae350.sourceprep import prepare, installer_inputs
from scripts.modular.machines.mega138k_ae350.opensbi import (
    compile_dtb, check_firmware_end, check_no_atomics, saved_config, native)

LEGACY_SOURCE = '$HOME/uboot-port'
DEFCONFIG = 'mega138k_ae350_defconfig'
# U-Boot is compiled by the GNU cross compiler for its rv32 no-A multilib libgcc
# and linked by lld (the ELF binutils refuse -pie); host tools use the gcc
# wrapper, whose libc headers precede U-Boot's -idirafter include.
TOOLCHAIN = ['ARCH=riscv', 'CROSS_COMPILE=riscv-none-elf-', 'LD=ld.lld', 'HOSTCC=gcc', 'DTC=dtc']


def contract_config(layout):
    """Link, stack and load addresses are the os.uboot and os.kernel contract ranges."""
    return {'TEXT_BASE': layout.OS_UBOOT_BASE,
            'CUSTOM_SYS_INIT_SP_ADDR': layout.OS_UBOOT_BASE + layout.OS_UBOOT_BYTES,
            'SYS_LOAD_ADDR': layout.OS_KERNEL_BASE}


def check_uboot(elf, layout):
    symbols = subprocess.run(['llvm-nm', str(elf)], check=True, capture_output=True, text=True).stdout
    found = {name: int(value, 16) for value, _, name in
             (line.split() for line in symbols.splitlines() if len(line.split()) == 3)}
    if found.get('_start') != layout.OS_UBOOT_BASE:
        raise ValueError('U-Boot link address differs from the address contract')
    # The initial stack, global data and early malloc grow down from the top of os.uboot.
    end = max(found['_end'], found['__bss_end'])
    if end > layout.OS_UBOOT_BASE + layout.OS_UBOOT_BYTES // 2:
        raise ValueError('U-Boot image leaves less than half of os.uboot for its initial stack')
    return end - found['_start']


def configure(source, output, layout, log):
    config = output/'.config'
    subprocess.run(['make', '-C', str(source), 'O='+str(output), *TOOLCHAIN, DEFCONFIG],
                   check=True, stdout=log, stderr=log)
    values = contract_config(layout)
    subprocess.run([str(source/'scripts/config'), '--file', str(config),
                    *[arg for name, value in values.items() for arg in ('--set-val', name, hex(value))]],
                   check=True, stdout=log, stderr=log)
    subprocess.run(['make', '-C', str(source), 'O='+str(output), *TOOLCHAIN, 'olddefconfig'],
                   check=True, stdout=log, stderr=log)
    actual = dict(line.split('=', 1) for line in config.read_text().splitlines()
                  if line.startswith('CONFIG_') and '=' in line)
    for name, value in values.items():
        if int(actual.get('CONFIG_' + name, '0'), 0) != value:
            raise ValueError('U-Boot CONFIG_%s differs from the AE350 address contract' % name)
    for name in ('RISCV_ISA_ZAAMO', 'RISCV_ISA_ZALRSC', 'AVAILABLE_HARTS', 'SMP'):
        if actual.get('CONFIG_' + name) == 'y':
            raise ValueError('U-Boot CONFIG_%s would emit atomics on the no-AMO AE350' % name)
    return config


def build(store, run, args, dry):
    source = (store.build/'vendor/u-boot-ae350' if args.source == LEGACY_SOURCE
              else Path(args.source)).resolve()
    opensbi = (store.build/'vendor/opensbi').resolve()
    work = location(store, run, 'mega138k_ae350', 'uboot')
    output = work/'obj'
    payload_output = work/'opensbi-payload'
    layout = AddressMap(resolve_machine('mega138k_ae350'))
    layout.require()
    recipe = {p: file_hash(store.root/p) for p in (
        'firmware/uboot/ae350/source.json', 'firmware/opensbi/ae350/source.json',
        'firmware/opensbi/ae350/opensbi.config', 'firmware/dts/mega138k_ae350.dts',
        'firmware/dts/mega138k_ae350-u-boot.dts')}
    for name in ('uboot', 'opensbi'):
        recipe.update({str(p.relative_to(store.root)): file_hash(p) for p in installer_inputs(store, name)})
    run.result.update(source=str(source), opensbi_source=str(opensbi), output_dir=str(output),
                      recipe_sha256=recipe, software_layout=layout.software_layout(),
                      contract_config={k: hex(v) for k, v in contract_config(layout).items()})
    if dry:
        prepare(store, run, 'uboot', source, dry=True)
        run.finish(plan=True)
        return
    key = hashlib.sha256(str((recipe, layout.software_layout())).encode()).hexdigest()
    with working(store, run, 'mega138k_ae350', 'uboot', key) as workspace:
        prepare(store, run, 'uboot', source)
        run.result['uboot_source'] = run.result.pop('upstream_source')
        prepare(store, run, 'opensbi', opensbi)
        run.result['opensbi_source'] = run.result.pop('upstream_source')
        output.mkdir(parents=True, exist_ok=True)
        payload_output.mkdir(parents=True, exist_ok=True)
        with (run.path/'build.log').open('w') as log:
            control = compile_dtb(store, layout, work, log, 'mega138k_ae350-u-boot')
            linux_dtb = compile_dtb(store, layout, work, log)
            config = configure(source, output, layout, log)
            # The environment and board header include the contract from the build's include directory.
            header = output/'include/ae350_dts.h'
            header.parent.mkdir(parents=True, exist_ok=True)
            if not header.exists() or header.read_text() != layout.dts_header():
                header.write_text(layout.dts_header())
            command = ['make', '-C', str(source), 'O='+str(output), *TOOLCHAIN,
                       'EXT_DTB='+str(control), '-j8']
            run.result['native_command'] = command
            run.result['compiler'] = subprocess.run(['riscv-none-elf-gcc', '--version'], check=True,
                                                    capture_output=True, text=True).stdout.splitlines()[0]
            subprocess.run(command, check=True, stdout=log, stderr=log)
            run.result['uboot_bytes'] = check_uboot(output/'u-boot', layout)
            check_no_atomics(output/'u-boot', 'U-Boot')
            saved, _ = saved_config(store, payload_output)
            run.result['opensbi_config_sha256'] = file_hash(saved)
            run.result['opensbi_command'] = native(store, opensbi, payload_output, layout, control, log,
                                                   payload=output/'u-boot.bin')
        firmware = payload_output/'platform/generic/firmware'
        run.result['opensbi_bytes'] = check_firmware_end(firmware/'fw_payload.elf', layout)
        check_no_atomics(firmware/'fw_payload.elf', 'fw_payload')
        binary = firmware/'fw_payload.bin'
        if binary.stat().st_size > layout.OS_SBI_BYTES + layout.OS_UBOOT_BYTES:
            raise ValueError('fw_payload.bin exceeds the os.sbi + os.uboot load range')
        artifact, delivery = archive(store, run, args, workspace, {
            'fw_payload.bin': binary, 'u-boot.bin': output/'u-boot.bin',
            'mega138k_ae350-u-boot.dtb': control, 'mega138k_ae350.dtb': linux_dtb,
            'u-boot.config': config})
        run.finish(artifact=artifact, binary=str(delivery/'fw_payload.bin'), sha256=file_hash(binary),
                   dtb=str(delivery/'mega138k_ae350.dtb'), dtb_sha256=file_hash(linux_dtb),
                   qualification='compiled-not-board-tested')
