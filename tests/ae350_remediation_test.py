"""Regression cases for independently reproduced P2 review failures."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from migen.sim import run_simulation
from migen import Module, Signal, Cat, Constant, ClockDomainsRenamer
from migen.fhdl.specials import Memory
from migen.genlib.cdc import BusSynchronizer
from scripts.tests.test_ddr3_ahb import DUT, Model, Mon, ahb
from gateware.machines.ddr3_ahb import DDR3AHB, DDR3UI
from gateware.machines.ae350_native import AHBMemory
from gateware.machines.ae350_interconnect import MemInterconnect, ErrorSlave
from litex.soc.interconnect.ahb import AHBInterface
from scripts.tests.test_ae350_interconnect import DUT as InterconnectDUT, xact
from mini_ops.ae350_p2_ops import RunRequest, _engine_args
from modular import ae350
from modular.storage import Store
import ae350_report
import ae350_image
from programmer_cable import resolve_location
from mini_ops import ae350_upload
from mini_ops.ae350_session import HardwareSession, SessionError
from mini_ops.ae350_jtag import CPUJTAG
from mini_ops.ae350_payload import payload_file, application_parameters
from argparse import Namespace


class AdapterRecoveryTests(unittest.TestCase):
    def test_production_memory_and_decoder_with_real_cdc_and_current_map(self):
        from gateware.machines import ae350_map
        self.assertEqual((ae350_map.DDR_BASE, ae350_map.DLM_BASE), (0, 0xa0200000))
        self.assertFalse(any(name == 'sram' for name, *_ in ae350_map.WINDOWS))
        dut = Module()
        master, sram, ddr, error = [AHBInterface(data_width=64) for _ in range(4)]
        ui = DDR3UI()
        dut.submodules.sram = AHBMemory(sram, 8192, base=0x40000000)
        dut.submodules.ddr = DDR3AHB(ddr, ui, Constant(1), base=0, accepted_i=ddr.trans[1])
        dut.submodules.error = ErrorSlave(error)
        dut.submodules.ic = MemInterconnect(master, [(0, 1 << 30, ddr),
            (0x40000000, 8192, sram), (0, 1 << 32, error)])
        dut.submodules.mon = mon = ClockDomainsRenamer({'sys': 'ddr'})(Mon(ui))
        model = Model(ui, mon)
        fragment = dut.get_fragment()
        for memory in fragment.specials:
            if isinstance(memory, Memory):
                for port in memory.ports:
                    if port.dat_r is None:
                        port.dat_r = Signal(memory.width)
        def transfer(write, address, data=0):
            yield master.addr.eq(address)
            yield master.write.eq(write)
            yield master.size.eq(3)
            yield master.trans.eq(2)
            yield master.wdata.eq(0xbad0bad0bad0bad0)
            yield
            yield master.trans.eq(0)
            yield master.wdata.eq(data)
            yield
            for _ in range(300):
                if (yield master.readyout):
                    result = ((yield master.resp), (yield master.rdata))
                    yield
                    return result
                yield
            self.fail('production memory transaction timed out')
        def process():
            for address, value in [(0, 0x1122334455667788), (0x40000000, 0x8877665544332211),
                                   (24, 0xaabbccddeeff0011), (0x40000800, 0x123456789abcdef0)]:
                response, _ = yield from transfer(1, address, value)
                self.assertEqual(response, 0)
            for address, value in [(24, 0xaabbccddeeff0011), (0x40000800, 0x123456789abcdef0),
                                   (0, 0x1122334455667788), (0x40000000, 0x8877665544332211)]:
                response, data = yield from transfer(0, address)
                self.assertEqual((response, data), (0, value))
            response, _ = yield from transfer(0, 0x7ffff000)
            self.assertEqual(response, 1)
            response, data = yield from transfer(0, 0x40000000)
            self.assertEqual((response, data), (0, 0x8877665544332211))
            self.assertEqual(len(model.events), 4)
        run_simulation(fragment, {'sys': process(), 'ddr': model.run()},
                       clocks={'sys': 12, 'ddr': 10})

    def test_32_bit_bus_selects_all_word_lanes(self):
        dut = DUT(data_width=32)
        model = Model(dut.ui, dut.mon)
        def process():
            for i in range(8):
                response, _ = yield from ahb(dut.bus, True, 0x2000 + i*4, 0x12340000+i)
                self.assertEqual(response, 0)
            for i in range(8):
                response, data = yield from ahb(dut.bus, False, 0x2000 + i*4)
                self.assertEqual((response, data), (0, 0x12340000+i))
            response, _ = yield from ahb(dut.bus, False, 0x2000, size=3)
            self.assertEqual(response, 1)
        run_simulation(dut, {'sys': process(), 'ddr': model.run()},
                       clocks={'sys': 10, 'ddr': 10})

    def test_new_address_accepted_by_decoder_during_drain_is_served(self):
        dut = InterconnectDUT()
        model = Model(dut.ui, dut.mon)
        model.mem[0] = 0xaaaaaaaa
        model.mem[8] = 0xbbbbbbbb
        model.delay_read = 75
        def process():
            response, _ = yield from xact(dut.master, False, 0x2000)
            self.assertEqual(response, 1)
            response, data = yield from xact(dut.master, False, 0x2020)
            self.assertEqual((response, data & 0xffffffff), (0, 0xbbbbbbbb))
            for _ in range(15):
                yield
            self.assertEqual(len(model.events), 2)
            self.assertEqual((yield dut.ic.n_accepted), 2)
        run_simulation(dut, {'sys': process(), 'ddr': model.run()},
                       clocks={'sys': 10, 'ddr': 10})

    def test_real_async_fifos_with_unrelated_clocks(self):
        dut = DUT(fifo_cls=None)
        model = Model(dut.ui, dut.mon)
        fragment = dut.get_fragment()
        # Migen's simulator tries to read write-only memory ports. Supply an
        # unused read sink, without replacing the actual FIFO, pointers or CDC.
        for memory in fragment.specials:
            if isinstance(memory, Memory):
                for port in memory.ports:
                    if port.dat_r is None:
                        port.dat_r = Signal(memory.width)
        def process():
            for i in range(8):
                response, _ = yield from ahb(dut.bus, True, 0x2000 + i*8, 0xabcdef00 + i)
                self.assertEqual(response, 0)
            for i in range(8):
                response, data = yield from ahb(dut.bus, False, 0x2000 + i*8)
                self.assertEqual((response, data), (0, 0xabcdef00 + i))
            self.assertEqual(len(model.events), 16)
        run_simulation(fragment, {'sys': process(), 'ddr': model.run()},
                       clocks={'sys': 10, 'ddr': 14})

    def test_diagnostic_snapshot_is_coherent(self):
        dut = Module()
        dut.submodules.transfer = transfer = BusSynchronizer(64, 'ddr', 'sys')
        counter = Signal(32)
        dut.sync.ddr += counter.eq(counter + 1)
        dut.comb += transfer.i.eq(Cat(counter, ~counter))
        def process():
            for _ in range(100):
                yield
            for _ in range(100):
                value = yield transfer.o
                self.assertEqual(value >> 32, (~value) & 0xffffffff)
                yield
        run_simulation(dut, process(), clocks={'sys': 10, 'ddr': 14})

    def test_dead_transfer_completes_with_error(self):
        dut = DUT()
        dut.ddr.fsm.reset_state = 'DEAD'
        def process():
            for address in (0x2000, 0x2008):
                response, _ = yield from ahb(dut.bus, False, address)
                self.assertEqual(response, 1)
        run_simulation(dut, process(), clocks={'sys': 10, 'ddr': 10})

    def test_idle_after_timeout_never_creates_a_request(self):
        dut = DUT()
        model = Model(dut.ui, dut.mon)
        model.delay_read = 75
        def process():
            response, _ = yield from ahb(dut.bus, False, 0x2000)
            self.assertEqual(response, 1)
            for _ in range(180):
                yield
            self.assertEqual(len(model.events), 1, model.events)
            self.assertEqual((yield dut.ddr.recovering), 0)
            self.assertEqual((yield dut.bus.readyout), 1)
        run_simulation(dut, {'sys': process(), 'ddr': model.run()},
                       clocks={'sys': 10, 'ddr': 10})


class HostContractTests(unittest.TestCase):
    def test_user_source_changes_only_payload_inputs(self):
        store = Store(ROOT)
        args = Namespace(source=None)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(store, 'lookup', return_value=None), \
             patch.object(ae350, 'discover', return_value={}), \
             patch.object(ae350, 'identity', return_value={}):
            source = Path(directory) / 'app.c'
            source.write_text('void main(void) { for (;;) {} }')
            args.source = source
            ae350.firmware(store, Mock(), {}, plan=True, profile='p2')
            boot_before = store.lookup.call_args.args[1]
            ae350.payload(store, Mock(), {}, args, 'p2', plan=True)
            first = store.lookup.call_args.args[1]
            source.write_text('void main(void) { for (;;) __asm__("wfi"); }')
            ae350.payload(store, Mock(), {}, args, 'p2', plan=True)
            second = store.lookup.call_args.args[1]
            ae350.firmware(store, Mock(), {}, plan=True, profile='p2')
            boot_after = store.lookup.call_args.args[1]
        self.assertNotEqual(first, second)
        self.assertEqual(boot_before, boot_after)

    def test_payload_filename_is_owned_by_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'manifest.json').write_text(json.dumps({'config': {'binary': 'outputs/user.bin'}}))
            self.assertEqual(payload_file(root), root / 'outputs/user.bin')

    def test_d2xx_close_failure_is_not_silently_ignored(self):
        cpu = CPUJTAG.__new__(CPUJTAG)
        cpu.closed = False
        cpu.active = True
        cpu.resume = Mock()
        cpu.handle = object()
        cpu.dll = Mock()
        cpu.dll.FT_Close.return_value = 1
        with self.assertRaisesRegex(RuntimeError, 'D2XX status 1'):
            cpu.close()
        self.assertTrue(cpu.closed)
        cpu.close()
        cpu.dll.FT_Close.assert_called_once()

    def test_shared_cli_location_is_normalized_once(self):
        self.assertEqual(RunRequest(operation='run', build='selected', location='6705').validate().location, 6705)
        self.assertIsNone(RunRequest(operation='run', build='selected', location='auto').validate().location)

    def test_bootloader_cache_key_includes_runtime_abi(self):
        store = Store(ROOT)
        run = Mock()
        from gateware.machines import ae350_map
        with patch.object(store, 'lookup', return_value={'component': 'bootloader'}), \
             patch.object(ae350, 'discover', return_value={}), \
             patch.object(ae350, 'identity', return_value={}), \
             patch.object(ae350_map, 'image_abi', side_effect=[111, 222]):
            ae350.firmware(store, run, {}, plan=True, profile='p2')
            first = store.lookup.call_args.args[1]
            ae350.firmware(store, run, {}, plan=True, profile='p2')
            second = store.lookup.call_args.args[1]
        self.assertNotEqual(first, second)

    def test_incomplete_receipt_is_not_a_failed_test(self):
        self.assertEqual(ae350_report.classify('payload', {'passed': False})['state'], 'in-progress')

    def test_handoff_does_not_replace_cpu_execution_evidence(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(ae350_report, 'ROOT', Path(directory)):
            root = Path(directory)
            for name, steps in [('001', [dict(name='cpu', phase='executing')]), ('002', [])]:
                path = root / ('build/runs/' + name + '-board-ae350-upload/result.json')
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(dict(passed=True, artifact={'id': 'same'}, steps=steps)))
            result = ae350_report.latest('board-ae350-upload', {'id': 'same'}, require_step='cpu')
            self.assertIn('001-board-ae350-upload', result['_path'])

    def test_minimum_pulse_width_is_a_build_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pnr = root / 'gateware/impl/pnr'
            pnr.mkdir(parents=True)
            (pnr / 'project_tr_content.html').write_text(
                'Numbers of Setup Violated Endpoints 0 Numbers of Hold Violated Endpoints 0')
            (pnr / 'project.tr').write_text('MPW Summary:\nSlack: -0.482\n')
            with self.assertRaisesRegex(ValueError, 'pulse width failed'):
                ae350.timing_report(root)
            (pnr / 'project.tr').write_text('MPW Summary:\nSlack: 0.61\n')
            self.assertEqual(ae350.timing_report(root)['min_pulse_width_slack_ns'], 0.61)

    def upload_fixture(self, location=None, pc=0x3000, program_error=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / 'outputs').mkdir()
        (root / 'outputs/tests.bin').write_bytes(b'\x13\0\0\0')
        (root / 'manifest.json').write_text(json.dumps({'config': {}}))
        op = Mock(path=root, report={})
        op.record.side_effect = lambda name, **values: op.report.setdefault('steps', []).append(dict(name=name, **values))
        hardware = Mock()
        hardware.program.side_effect = program_error
        hardware.halt.return_value.register.side_effect = lambda csr: pc if csr == 0x7b1 else 0
        hardware.halt.return_value.read_memory.return_value = bytes(4)
        session = Mock()
        session.fresh_banner.return_value = b'riscv-mini AE350 ROM expected\r\nPOST 600d0000\r\nDDR INIT OK\r\nBL> '
        session.upload.return_value = b'SYSTEM READY'
        store = Mock()
        store.get.return_value = dict(dependencies={'bootloader': {}},
                                     config={'image_abi': ae350_image.abi_tag()})
        for target, value in [
            ('mini_ops.ae350_upload.Store', Mock(return_value=store)),
            ('mini_ops.ae350_upload.Operation', Mock(return_value=op)),
            ('mini_ops.ae350_upload.candidate', Mock(return_value=(root, {}, 'expected'))),
            ('mini_ops.ae350_upload.resolve_payload', Mock(return_value=({}, root))),
            ('mini_ops.ae350_upload.BootSession', Mock(return_value=session)),
            ('mini_ops.ae350_session.HardwareSession', Mock(return_value=hardware)),
            ('mini_ops.ae350_upload.time.sleep', Mock())]:
            manager = patch(target, value)
            manager.start()
            self.addCleanup(manager.stop)
        args = Namespace(build='selected', payload='selected', port='COM9', location=6705,
                         cpu_jtag_location=location, repeat=1, timeout=30, self_test=False, dry_run=False)
        return args, op, hardware

    def test_uart_handoff_does_not_claim_execution_without_evidence(self):
        args, op, hardware = self.upload_fixture()
        ae350_upload.perform_upload(args)
        self.assertEqual(op.report['phase'], 'uploaded-and-handoff')
        hardware.close.assert_called_once()

    def test_hardware_abi_mismatch_is_rejected_before_programming(self):
        args, op, hardware = self.upload_fixture()
        with patch('mini_ops.ae350_upload.Store') as store:
            store.return_value.get.return_value = dict(dependencies={'bootloader': {}}, config={'image_abi': 0})
            with self.assertRaisesRegex(ValueError, 'different address ABI'):
                ae350_upload.perform_upload(args)
        hardware.program.assert_not_called()

    def test_pc_outside_actual_image_is_not_execution(self):
        args, op, hardware = self.upload_fixture(location=107569, pc=0x70000)
        with self.assertRaisesRegex(RuntimeError, 'did not execute'):
            ae350_upload.perform_upload(args)
        self.assertFalse(op.report['passed'])
        hardware.close.assert_called_once()

    def test_program_failure_still_closes_session(self):
        args, op, hardware = self.upload_fixture(program_error=RuntimeError('program failed'))
        with self.assertRaisesRegex(RuntimeError, 'program failed'):
            ae350_upload.perform_upload(args)
        hardware.close.assert_called_once()
        self.assertFalse(op.report['passed'])

    def test_zero_uploads_rejected_before_hardware(self):
        args, op, hardware = self.upload_fixture()
        args.repeat = 0
        with self.assertRaises(ValueError):
            ae350_upload.perform_upload(args)
        hardware.program.assert_not_called()

    def test_cleanup_failure_invalidates_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            op = Mock(path=Path(directory), report={'passed': True})
            session = HardwareSession(op)
            session.port = Mock()
            session.port.close.side_effect = OSError('close failed')
            with self.assertRaises(SessionError):
                session.close()
            self.assertFalse(op.report['passed'])
            self.assertTrue(op.report['session']['cleanup_errors'])

    def test_firmware_verify_validates_jtag_before_hardware(self):
        with self.assertRaisesRegex(ValueError, 'cpu-jtag-location'):
            RunRequest(operation='verify', build='selected', suite='firmware').validate()

    def test_explicit_programmer_location_survives_dispatch(self):
        request = RunRequest(operation='run', build='selected', location=6705)
        self.assertEqual(_engine_args(request).location, 6705)

    def test_p2_bootloader_hashes_address_contract(self):
        paths = ae350._inputs(Store(ROOT), profile='p2')
        self.assertIn('inputs/source/gateware/machines/ae350_map.py', paths)

    def test_oversize_image_is_rejected_before_hardware(self):
        with self.assertRaises(ValueError):
            ae350_image.build(bytes(0x7d001))

    def test_empty_evidence_cannot_pass_acceptance(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(ae350_report, 'ROOT', Path(directory)):
            result = ae350_report.summarize(None)
        self.assertTrue(all(v['state'] == 'untested' for v in result['acceptance'].values()))

    def test_receipt_path_belongs_to_selected_run(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(ae350_report, 'ROOT', Path(directory)):
            root = Path(directory)
            for name in ('001', '002'):
                path = root / ('build/runs/' + name + '-board-ae350-upload/result.json')
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(dict(passed=True, artifact={'id': name})))
            selected = ae350_report.latest('board-ae350-upload', {'id': '001'})
            self.assertIn('001-board-ae350-upload', selected['_path'])

    def test_multiple_matching_boards_require_explicit_location(self):
        import subprocess
        def completed(text):
            return subprocess.CompletedProcess([], 0, text, '')
        runner = Mock(side_effect=[completed('Cable A/0/1/null\nCable A/0/2/null'),
                                   completed('ID: 0x0001081B\n1 device(s) found'),
                                   completed('ID: 0x0001081B\n1 device(s) found')])
        with self.assertRaisesRegex(RuntimeError, 'Multiple matching'):
            resolve_location('programmer', identity=0x1081b, runner=runner)


class ApplicationParameterTests(unittest.TestCase):
    def fixture(self, root, offset=8):
        (root / 'app.bin').write_bytes(bytes(range(64)))
        (root / 'manifest.json').write_text(json.dumps({'config': {
            'binary': 'app.bin', 'parameters': 'params.json'}}))
        layout = dict(offset=offset, size=20, format='5I-le',
                      fields=['stress_seconds', 'seed', 'region_kb', 'flags', 'traversal_mb'])
        (root / 'params.json').write_text(json.dumps(layout))
        return dict(stress_seconds=2, seed=0xabcdef01, region_kb=1024, flags=15, traversal_mb=1)

    def test_only_declared_bytes_change_and_uart_crc_covers_parameters(self):
        import struct
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = self.fixture(root)
            data = application_parameters(root, values)
            self.assertEqual(data[:8], bytes(range(8)))
            self.assertEqual(data[28:], bytes(range(28, 64)))
            self.assertEqual(struct.unpack('<5I', data[8:28]), tuple(values.values()))
            _, decoded = ae350_image.parse(ae350_image.build(data))
            self.assertEqual(decoded, data)
            self.assertEqual((root / 'app.bin').read_bytes(), bytes(range(64)))

    def test_bad_parameter_offsets_are_rejected(self):
        for offset in (-1, 45, 1000):
            with self.subTest(offset=offset), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                values = self.fixture(root, offset)
                with self.assertRaisesRegex(ValueError, 'outside'):
                    application_parameters(root, values)

    def test_unknown_or_missing_parameters_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = self.fixture(root)
            values['entry'] = 0x1234
            with self.assertRaisesRegex(ValueError, 'contract'):
                application_parameters(root, values)

    def test_no_unsigned_wrapping_of_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = self.fixture(root)
            values['seed'] = -1
            with self.assertRaisesRegex(ValueError, 'unsigned'):
                application_parameters(root, values)


if __name__ == '__main__':
    unittest.main()
