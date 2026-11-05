"""Build the AE350 native OpenSBI tree through the common build entry."""
import hashlib
import json
import re
import subprocess

from gateware.description import resolve_machine
from gateware.machines.mega138k_ae350.address_map import AddressMap
from scripts.modular.storage import source_file_hash, file_hash
from scripts.modular.workspace import working, location
from scripts.modular.machines.mega138k_ae350.upstream import archive
from scripts.modular.machines.mega138k_ae350.sourceprep import prepare

ATOMIC_INSTRUCTION = re.compile(r'^\s*[0-9a-f]+:\s+((?:amo[a-z]+|lr|sc)\.[wd][.a-z]*)\s', re.M)


def compile_dtb(store, layout, work, log, name='mega138k_ae350'):
    """Preprocess the DTS with the address contract, then reject unit-address/reg mismatches."""
    header = work/'ae350_dts.h'
    text = layout.dts_header()
    if not header.exists() or header.read_text() != text:
        header.write_text(text)
    source = store.root/'firmware/dts'/(name + '.dts')
    preprocessed = work/(name + '.pp.dts')
    dtb = work/(name + '.dtb')
    subprocess.run(['clang', '-E', '-P', '-nostdinc', '-undef', '-x', 'assembler-with-cpp',
                    '-I', str(work), '-o', str(preprocessed), str(source)], check=True, stdout=log, stderr=log)
    subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb', '-E', 'unit_address_vs_reg', '-E', 'simple_bus_reg',
                    '-o', str(dtb), str(preprocessed)], check=True, stdout=log, stderr=log)
    tree = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', str(dtb)], check=True,
                          capture_output=True, text=True).stdout
    check_unit_addresses(tree)
    return dtb


def check_unit_addresses(tree):
    """Every node@address must name the first address of its own reg (single-cell layout)."""
    stack = []
    for line in tree.splitlines():
        text = line.strip()
        if text.endswith('{'):
            stack.append(text[:-1].strip().split(':')[-1].strip())
        elif text.startswith('};'):
            stack.pop()
        elif text.startswith('reg = <') and stack and '@' in stack[-1]:
            first = text[len('reg = <'):].split()[0].rstrip('>;')
            if int(first, 0) != int(stack[-1].split('@')[1], 16):
                raise ValueError('Device tree node %s does not match its reg' % stack[-1])


def check_firmware_end(elf, layout):
    symbols = subprocess.run(['llvm-nm', str(elf)], check=True, capture_output=True, text=True).stdout
    found = {name: int(value, 16) for value, _, name in
             (line.split() for line in symbols.splitlines() if len(line.split()) == 3)}
    if found.get('_fw_start') != layout.OS_SBI_BASE:
        raise ValueError('OpenSBI link address differs from the address contract')
    if found['_fw_end'] > layout.OS_SBI_BASE + layout.OS_SBI_BYTES:
        raise ValueError('OpenSBI image exceeds the reserved os.sbi range')
    return found['_fw_end'] - found['_fw_start']


def check_no_atomics(elf, name='OpenSBI'):
    """DDR AMO/LR/SC retry forever unless the line is read-allocated in L1D; the image must contain none."""
    listing = subprocess.run(['llvm-objdump', '-d', '--no-show-raw-insn', str(elf)], check=True,
                             capture_output=True, text=True).stdout
    found = ATOMIC_INSTRUCTION.findall(listing)
    if found:
        raise ValueError('%s image contains atomic instructions: %s' % (name, found[:4]))


def saved_config(store, output):
    saved = store.root/'firmware/opensbi/ae350/opensbi.config'
    config_path = output/'platform/generic/kconfig/.config'
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists() or config_path.read_bytes() != saved.read_bytes():
        config_path.write_bytes(saved.read_bytes())
    return saved, config_path


def native(store, source, output, layout, dtb, log, payload=None):
    """fw_jump enters the Linux Image directly; fw_payload carries U-Boot at os.uboot."""
    command = ['make', '-C', str(source), 'O='+str(output), 'PLATFORM=generic',
               'LLVM=1', 'PLATFORM_RISCV_XLEN=32', 'PLATFORM_RISCV_ABI=ilp32',
               'PLATFORM_RISCV_ISA=rv32imfd_zicsr_zifencei',
               'OPENSBI_VERSION_GIT=%s-ae350' % json.loads(
                   (store.root/'firmware/opensbi/ae350/source.json').read_text())['version'][:12],
               'FW_DYNAMIC=n', 'FW_TEXT_START=0x%x' % layout.OS_SBI_BASE]
    goals = []
    if payload is None:
        command += ['FW_PAYLOAD=n', 'FW_JUMP_ADDR=0x%x' % layout.OS_KERNEL_BASE,
                    'FW_JUMP_FDT_ADDR=0x%x' % layout.OS_DTB_BASE]
    else:
        command += ['FW_JUMP=n', 'FW_PAYLOAD=y', 'FW_PAYLOAD_PATH='+str(payload),
                    'FW_PAYLOAD_OFFSET=0x%x' % (layout.OS_UBOOT_BASE - layout.OS_SBI_BASE),
                    'FW_PAYLOAD_FDT_ADDR=0x%x' % layout.OS_FIRMWARE_FDT_BASE]
        # FW_PAYLOAD=y also lists upstream's test payload, which needs atomics.
        firmware = output/'platform/generic/firmware'
        goals = [str(firmware/'fw_payload.elf'), str(firmware/'fw_payload.bin')]
    command += ['FW_FDT_PATH='+str(dtb)]
    # OpenSBI tracks neither flags nor a moved source tree; objects from another command are stale.
    stamp = output/'.mini-native-command'
    if not stamp.exists() or stamp.read_text() != '\n'.join(command):
        stamp.unlink(missing_ok=True)
        subprocess.run(command+['clean'], check=True, stdout=log, stderr=log)
    subprocess.run(command+['-j8']+goals, check=True, stdout=log, stderr=log)
    stamp.write_text('\n'.join(command))
    return command


def build(store, run, args, dry):
    source = args.source.resolve()
    work = location(store, run, 'mega138k_ae350', 'opensbi')
    output = work/'obj'
    layout = AddressMap(resolve_machine('mega138k_ae350'))
    layout.require()
    inputs = {str(p.relative_to(source)): source_file_hash(p)
              for p in source.rglob('*') if p.is_file() and (p.suffix in ('.c', '.h', '.S', '.mk') or p.name in ('Makefile', 'Kconfig', 'defconfig'))
              and '.git' not in p.parts and 'build' not in p.relative_to(source).parts}
    run.result.update(source=str(source), source_inputs=inputs, output_dir=str(output),
                      dtb_source_sha256=file_hash(store.root/'firmware/dts/mega138k_ae350.dts'),
                      software_layout=layout.software_layout())
    if dry:
        prepare(store, run, 'opensbi', source, dry=True)
        run.finish(plan=True)
        return
    key = hashlib.sha256(str((inputs, layout.software_layout(), run.result['dtb_source_sha256'])).encode()).hexdigest()
    with working(store, run, 'mega138k_ae350', 'opensbi', key) as workspace:
        prepare(store, run, 'opensbi', source)
        output.mkdir(parents=True, exist_ok=True)
        saved, config_path = saved_config(store, output)
        run.result['config_sha256'] = file_hash(saved)
        with (run.path/'build.log').open('w') as log:
            dtb = compile_dtb(store, layout, work, log)
            run.result['native_command'] = native(store, source, output, layout, dtb, log)
        firmware = output/'platform/generic/firmware'
        run.result['image_bytes'] = check_firmware_end(firmware/'fw_jump.elf', layout)
        check_no_atomics(firmware/'fw_jump.elf')
        binary = firmware/'fw_jump.bin'
        artifact, delivery = archive(store, run, args, workspace,
                                     {'fw_jump.bin':binary, 'mega138k_ae350.dtb':dtb, 'config':config_path})
        run.finish(artifact=artifact, binary=str(delivery/'fw_jump.bin'), sha256=file_hash(binary), dtb_sha256=file_hash(dtb),
                   qualification='compiled-not-board-tested')
