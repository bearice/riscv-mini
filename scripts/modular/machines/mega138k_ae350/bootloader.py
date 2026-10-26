"""Independent AE350 boot ROM build; no gateware input closure."""
from scripts.modular.storage import digest, canonical, source_file_hash, SOURCE_HASH_FORMAT, text_file
from scripts.modular.workspace import working
from scripts.modular.environment import discover, identity, output
from scripts.modular.compile import BASE_FLAGS, ROLES, ninja_build
BOOTLOADER = 'firmware/bootloader/mega138k_ae350'
PROFILES = ('ddr',)


def firmware(store, run, cpu, plan=False, profile='ddr'):
    if profile not in PROFILES:
        raise ValueError('Unknown AE350 profile: ' + profile)
    # The boot ROM depends only on its own source, compiler recipe and stable
    # reset contract. Neither the CPU artifact nor unrelated devices are inputs.
    names = ['scripts/modular/machines/mega138k_ae350/bootloader.py',
             'scripts/modular/compile.py', 'scripts/modular/environment.py',
             'scripts/modular/storage.py', 'scripts/modular/workspace.py',
             'scripts/modular/worker.py',
             'gateware/machines/mega138k_ae350/boot_contract.py',
             *[p.relative_to(store.root).as_posix() for p in sorted((store.root / BOOTLOADER).iterdir()) if p.is_file()]]
    files = {'inputs/source/' + name: store.root / name for name in names}
    hashes = {name: source_file_hash(path) for name, path in files.items()}
    env = discover(ROLES, store.root)
    from gateware.description import from_data
    description = store.get(store.get(cpu)['dependencies']['machine-description'])['config']
    machine_config = from_data(description['board'], description['machine'], description.get('ip_artifacts', {}))
    flags = [*BASE_FLAGS, '-march=rv32ima_zicsr_zifencei', '-mabi=' + machine_config.cpu.abi, '-mcmodel=medany', '-mno-relax',
             '-fno-delete-null-pointer-checks', '-Wl,--build-id=none']
    from gateware.machines.mega138k_ae350.address_map import AddressMap
    ae350_map = AddressMap(machine_config)
    ae350_map.require()
    from gateware.machines.mega138k_ae350 import boot_contract
    boot_contract.validate(ae350_map)
    config = dict(schema=1, storage_policy='outputs-only', fpga=dict(device=machine_config.board.fpga.device, part=machine_config.board.fpga.part), machine='mega138k_ae350', profile='AE350-ROM-bootloader',
                  purpose='AE350-ROM-bootloader',
                  source_hash_format=SOURCE_HASH_FORMAT, sources=hashes, flags=flags, tools=identity(env),
                  rom_base=ae350_map.ROM_BASE, rom_bytes=ae350_map.ROM_SIZE,
                  flash_bytes=ae350_map.FLASH_BYTES,
                  dlm_base=ae350_map.DLM_BASE, dlm_bytes=ae350_map.DLM_SIZE,
                  results_base=ae350_map.RESULTS_BASE,
                  uart_base=0xf0300000, uart_clock='SYSINFO+0x10', image_abi_source='SYSINFO+0x14',
                  boot_contract_version=boot_contract.VERSION,
                  payload_base=ae350_map.DDR_CODE,
                  payload_bytes=ae350_map.DDR_CODE_MAX,
                  payload_stack_top=ae350_map.PAYLOAD_STACK_TOP,
                  local_memory=dict(ilm_base=ae350_map.ILM_BASE, ilm_bytes=ae350_map.ILM_SIZE,
                                    dlm_base=ae350_map.DLM_BASE, dlm_bytes=ae350_map.DLM_SIZE,
                                    rom_private_stack='DLM', rom_hot_code='ILM', ecc_check='disabled'),
                  qualification='compiled-not-board-tested')
    key = digest(config)
    macro = 'MINI_AE350_BOOT_BUILD_ID'
    config = dict(config, build_id=key[:12], resolved_flags=flags, identity_macro=macro)
    found = store.lookup('bootloader', key)
    if found:
        run.step('bootloader', 'REUSE', found); return found
    if plan:
        run.step('bootloader', 'BUILD', reason='AE350 platform ROM bootloader inputs'); return None
    with working(store, run, 'mega138k_ae350', 'bootloader', key) as workspace:
        work = workspace.path
        ae350_map.require()
        gen = work / 'generated'
        gen.mkdir(parents=True, exist_ok=True)
        text_file(gen / 'ae350_boot_contract.h', boot_contract.c_header(ae350_map.ROM_SIZE))
        text_file(gen / 'ae350_boot_contract.ld', boot_contract.ld_fragment(ae350_map.ROM_SIZE))
        text_file(gen / 'identity.h', '#define %s "%s"\n' % (macro, key[:12]))
        flags = [*flags, '-I' + str(gen), '-Wl,-L,' + str(gen)]
        bsp = store.root / BOOTLOADER
        ninja_build(work, [bsp / name for name in ('start.S', 'main.c')], flags, env, 'boot',
                    linker=bsp / 'link.ld', run=run, root=store.root, identity_header=gen / 'identity.h')
        attributes = output([*env['bindings']['target.readelf'], '-h', '-A', str(work / 'boot.elf')])
        rom_limit = ae350_map.ROM_CODE_BYTES
        if '0x80000000' not in attributes or (work / 'boot.bin').stat().st_size > rom_limit:
            raise ValueError('Invalid AE350 ROM image entry or size (limit %d bytes)' % rom_limit)
        snapshot = {}
        import hashlib
        if any(source_file_hash(path) != hashes[name] for name, path in files.items()):
            raise ValueError('AE350 firmware inputs changed during build')
        snapshot.update({'outputs/boot.' + suffix: work / ('boot.' + suffix) for suffix in ('elf', 'bin', 'map')})
        snapshot['outputs/elf-attributes.txt'] = attributes.encode()
        snapshot['outputs/ae350_boot_contract.h'] = gen / 'ae350_boot_contract.h'
        snapshot['outputs/ae350_boot_contract.ld'] = gen / 'ae350_boot_contract.ld'
        snapshot['outputs/flash-layout.json'] = canonical(dict(flash='config-U4', flash_bytes=ae350_map.FLASH_BYTES,
            offset=boot_contract.FLASH_OFFSET, size=ae350_map.ROM_SIZE, erase_bytes=4096,
            image_bytes=(work / 'boot.bin').stat().st_size,
            cpu_base=ae350_map.ROM_BASE, controller='ae350-sdk-flash',
            programming='explicit-install-only'))
        snapshot['build.lock.json'] = canonical(config)
        found = store.publish('bootloader', key, snapshot, config=config, dependencies={})
        workspace.success(found)
    run.step('bootloader', 'BUILD', found); return found
