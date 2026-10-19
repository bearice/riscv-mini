"""SRAM-only JTAG launcher regression, with explicit immutable build identity."""
import struct
import time
from modular.storage import Store, file_hash
from mini_ops.ae350 import candidate
from mini_ops.board import Operation
from gateware.machines import ae350_map as amap
AE350_RESULTS = amap.SRAM_BASE + amap.SRAM_RESULTS
AE350_MAILBOX = amap.SRAM_BASE + amap.SRAM_MAILBOX
AE350_PROBE = amap.SRAM_BASE + amap.SRAM_PROBE
AE350_PROBE_LIMIT = AE350_PROBE + amap.SRAM_PROBE_BYTES
AE350_PROBE_STACK = amap.SRAM_STACK_TOP - 0x100


def probe_program():
    """Straight-line RV32I: distinct DDR stores, loads, SRAM results, then spin."""
    code = []
    def li(reg, value):
        code.extend([(((value + 0x800) >> 12) & 0xfffff) << 12 | reg << 7 | 0x37,
                     (value & 0xfff) << 20 | reg << 15 | reg << 7 | 0x13])
    def sw(src, base):
        code.append(src << 20 | base << 15 | 2 << 12 | 0x23)
    addresses = [0x2000, 0x2004, 0x2008, 0x200c, 0x2020, 0x2104, 0x3000, 0x3004]
    values = [0x11112222, 0x33334444, 0x55556666, 0x77778888,
              0x12345678, 0x89abcdef, 0x0f1e2d3c, 0x4b5a6978]
    for address, value in zip(addresses, values):
        li(5, address); li(6, value); sw(6, 5)
    code.append(0x0ff0000f)  # fence iorw,iorw
    for index, address in enumerate(addresses):
        li(5, address)
        code.append(5 << 15 | 2 << 12 | 6 << 7 | 3)  # lw t1,0(t0)
        li(7, AE350_RESULTS + 4 * index); sw(6, 7)
    li(6, 0xd0000002); li(7, (AE350_RESULTS + 0x40)); sw(6, 7)
    code.append(0x0000006f)
    return struct.pack('<' + 'I' * len(code), *code), addresses, values


def perform(args):
    op = Operation('ae350-jtag-test')
    print('Operation logs:', op.path, flush=True)
    cpu = None
    hardware = None
    try:
        root, ref, build_id = candidate(args.build)
        store = Store()
        manifest = store.get(ref)
        boot = store.path(manifest['dependencies']['bootloader'])
        expected_rom = (boot / 'outputs/boot.bin').read_bytes()
        fs = root / 'gateware/impl/pnr/project.fs'
        op.report.update(artifact=ref, fs_sha256=file_hash(fs), rom_build_id=build_id,
                         flash_written=False, qualification='basic-DDR-JTAG-launch-only')
        if args.dry_run:
            op.report.update(passed=True, dry_run=True)
            op.save()
            return op
        from mini_ops.ae350_session import HardwareSession
        hardware = HardwareSession(op, cpu_jtag_location=args.cpu_jtag_location,
                                   location=args.location)
        hardware.program(fs)
        cpu = hardware.halt()
        actual_rom = cpu.read_memory(0x80000000, len(expected_rom))
        (op.path / 'rom-readback.bin').write_bytes(actual_rom)
        if actual_rom != expected_rom:
            raise RuntimeError('Full ROM JTAG readback does not match selected firmware')
        # Nonsequential reads must not depend on the previously read address.
        for offset in [12, 0, 28, 4, 0, 16]:
            if cpu.read_memory(0x80000000 + offset, 4) != expected_rom[offset:offset + 4]:
                raise RuntimeError('Nonsequential ROM read mismatch')
        op.record('rom-match', bytes=len(expected_rom), nonsequential=True,
                  idcode=hex(cpu.idcode), cache_control=hex(cpu.register(0x7ca)))
        write_word = cpu.write_word
        payload, addresses, values = probe_program()
        assert AE350_PROBE + len(payload) <= AE350_PROBE_LIMIT, 'probe does not fit the reserved region'
        (op.path / 'probe.bin').write_bytes(payload)
        # Restart the ROM first: its POST uses SRAM scratch and would overwrite a
        # probe loaded before the restart.
        cpu.register(0x7b1, 0x80000000)
        cpu.resume(); time.sleep(.15); cpu.halt()
        monitor_pc = cpu.register(0x7b1)
        for offset in range(0, len(payload), 4):
            write_word(AE350_PROBE + offset, int.from_bytes(payload[offset:offset + 4], 'little'))
        for offset in range(0, 0x44, 4):
            write_word(AE350_RESULTS + offset, 0)
        write_word(AE350_MAILBOX, 0)
        write_word(AE350_MAILBOX + 4, AE350_PROBE); write_word(AE350_MAILBOX + 8, AE350_PROBE_STACK)
        write_word(AE350_MAILBOX, 0x5aa5c001)  # publish last
        cpu.resume()
        deadline = time.monotonic() + 3
        while True:
            time.sleep(.02); cpu.halt()
            done = int.from_bytes(cpu.read_memory((AE350_RESULTS + 0x40), 4), 'little')
            if done == 0xd0000002 or time.monotonic() >= deadline:
                break
            cpu.resume()
        got = list(struct.unpack('<8I', cpu.read_memory(AE350_RESULTS, 32)))
        ack = int.from_bytes(cpu.read_memory(AE350_MAILBOX, 4), 'little')
        registers = {name: hex(cpu.register(number)) for name, number in
                     [('pc', 0x7b1), ('mcause', 0x342), ('mepc', 0x341), ('mtval', 0x343)]}
        op.record('launcher-and-DDR', monitor_pc=hex(monitor_pc), ack=hex(ack),
                  done=hex(done), addresses=addresses, expected=values, actual=got,
                  registers=registers)
        if ack != 0xa55a0001 or done != 0xd0000002 or got != values:
            raise RuntimeError('JTAG launcher or CPU DDR probe failed; inspect operation result')
        op.report.update(passed=True)
        op.save()
        print('PASS ROM identity, nonsequential reads, launcher ACK, CPU DDR 8/8', flush=True)
        return op
    except Exception as error:
        op.report['error'] = str(error)
        raise
    finally:
        try:
            if cpu is not None:
                cpu.halt()
                cpu.write_word(AE350_MAILBOX, 0)
                cpu.register(0x7b1, amap.ROM_BASE)
        except Exception as error:
            op.report.update(passed=False, cleanup_error=str(error))
            raise
        finally:
            if hardware is not None:
                hardware.close()
            op.save()
