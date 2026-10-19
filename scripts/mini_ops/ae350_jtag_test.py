"""Read-only CPU debug qualification after programming an exact candidate.

Program loading belongs to UART. This checks the TAP, initialized ITCM bytes,
local-memory configuration, ROM POST and the platform status bank. No probe,
mailbox protocol, DPC redirection or program injection is used.
"""
from modular.storage import Store, file_hash
from mini_ops.ae350 import candidate
from mini_ops.board import Operation
from gateware.machines import ae350_map as amap


def perform(args):
    op = Operation('ae350-jtag-test')
    print('Operation logs:', op.path, flush=True)
    try:
        root, ref, build_id = candidate(args.build)
        store = Store()
        boot_ref = store.get(ref)['dependencies']['bootloader']
        boot = store.get(boot_ref)
        if boot['config'].get('image_abi') != amap.image_abi():
            raise ValueError('Selected candidate uses a different address ABI')
        expected = (store.path(boot_ref) / 'outputs/boot.bin').read_bytes()
        fs = root / 'gateware/impl/pnr/project.fs'
        op.report.update(artifact=ref, fs_sha256=file_hash(fs), rom_build_id=build_id,
                         flash_written=False, qualification='CPU-debug-and-local-memory-observation')
        if args.dry_run:
            op.report.update(passed=True, dry_run=True)
            return op
        from mini_ops.ae350_session import HardwareSession
        with HardwareSession(op, cpu_jtag_location=args.cpu_jtag_location,
                             location=args.location) as hardware:
            hardware.program(fs)
            import time
            time.sleep(.3)
            cpu = hardware.halt()
            actual = cpu.read_memory(amap.ROM_BASE, len(expected))
            (op.path / 'rom-readback.bin').write_bytes(actual)
            if actual != expected:
                raise RuntimeError('Initialized ITCM differs from the selected boot image')
            for offset in (12, 0, 28, 4, 0, 16):
                if cpu.read_memory(amap.ROM_BASE + offset, 4) != expected[offset:offset+4]:
                    raise RuntimeError('Nonsequential ITCM read mismatch')
            regs = {name: cpu.register(number) for name, number in
                [('milmb', 0x7c0), ('mdlmb', 0x7c1), ('micm_cfg', 0xfc0), ('mdcm_cfg', 0xfc1),
                 ('pc', 0x7b1), ('sp', 0x1002), ('mcause', 0x342), ('mepc', 0x341)]}
            op.record('rom-match', bytes=len(expected), nonsequential=True, idcode=hex(cpu.idcode))
            op.record('local-memory-config', **{name: hex(value) for name, value in regs.items()})
            if regs['milmb'] & 0xfffffc01 != amap.ILM_BASE | 1 or regs['mdlmb'] & 0xfffffc01 != amap.DLM_BASE | 1:
                raise RuntimeError('Unexpected hard ILM/DLM configuration')
            post = [int.from_bytes(cpu.read_memory(amap.RESULTS_BASE + i*4, 4), 'little') for i in range(12)]
            status = int.from_bytes(cpu.read_memory(amap.STATUS_ADDR, 4), 'little')
            op.record('autonomous-post', words=[hex(value) for value in post], status=hex(status))
            if post[0] != 0xd0b00001 or post[6] != 0 or post[7] != 0xd0b00002 or status & 7 != 3:
                raise RuntimeError('Autonomous POST/local memory status did not pass')
            if not amap.ROM_STACK_BOTTOM <= regs['sp'] <= amap.ROM_STACK_TOP or regs['mcause']:
                raise RuntimeError('ROM is not running cleanly with its DLM stack')
        op.report['passed'] = True
        print('PASS ITCM identity, CPU JTAG, hard ILM/DLM and autonomous POST', flush=True)
        return op
    except Exception as error:
        op.report.update(passed=False, error=str(error))
        raise
    finally:
        op.save()