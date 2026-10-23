"""Strict inputs and their native LiteX consumers share a single configuration."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from gateware.description import load_machine, resolve_machine, source_inputs, from_data
from gateware.gowin_platform import create_platform
from gateware.machines.primer20k_vexriscv.ports import bind_ports

SOFT = 'primer20k_vexriscv'
HARD = 'mega138k_ae350'


class PlatformDescriptionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for machine in (SOFT, HARD):
            for name in source_inputs(machine):
                destination = self.root / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, destination)

    def machine_path(self, machine=SOFT):
        return self.root / 'gateware/machines' / machine / 'machine.json'

    def test_closed_contracts(self):
        for machine in (SOFT, HARD):
            board, config = load_machine(machine, self.root)
            self.assertEqual(board['schema_version'], config['schema_version'])
            for key in (None, 'rtl', 'clocks', 'memory'):
                bad = copy.deepcopy(config)
                (bad if key is None else bad[key])['note'] = 'arbitrary prose'
                self.machine_path(machine).write_text(json.dumps(bad))
                with self.assertRaises(ValueError): load_machine(machine, self.root)
            self.machine_path(machine).write_text(json.dumps(config))

    def test_duplicate_keys(self):
        path = self.machine_path()
        path.write_text(path.read_text().replace('"schema_version": 2', '"schema_version": 2, "schema_version": 2'))
        with self.assertRaisesRegex(ValueError, 'Duplicate JSON property'): load_machine(SOFT, self.root)

    def test_cpu_features_have_one_source(self):
        board, config = load_machine(SOFT, self.root)
        config['ip']['cpu']['parameters'].update(mmu=True, fpu=True)
        machine = from_data(board, config)
        self.assertTrue(machine.rtl.features['mmu'])
        self.assertTrue(machine.rtl.features['fpu'])
        self.assertEqual(machine.rtl.cpu_variant, 'linux')
        self.assertIsNone(machine.rtl.cpu_verilog)
        config['rtl']['features']['mmu'] = False
        self.machine_path().write_text(json.dumps(config))
        with self.assertRaises(ValueError): load_machine(SOFT, self.root)

    def test_common_immutable_memory(self):
        soft, hard = (resolve_machine(name, self.root) for name in (SOFT, HARD))
        self.assertIs(type(soft.memory), type(hard.memory))
        self.assertEqual(soft.memory.size_bytes, 128 << 20)
        self.assertEqual(hard.memory.size_bytes, 1 << 30)
        with self.assertRaises(TypeError): soft.board.resources['clock'].signals['clk'] = None

    def test_clock_reaches_native_pll(self):
        board, config = load_machine(SOFT, self.root)
        config['clocks']['video'] = 12000000
        machine = from_data(board, config)
        from gateware.machines.primer20k_vexriscv.soc import ClockResetGenerator
        from gateware.machines.primer20k_vexriscv.features import Features
        platform = create_platform(bind_ports(machine), 'clk27', {})
        crg = ClockResetGenerator(platform, Features.resolve('minimal', {'video': True}), machine)
        self.assertEqual(crg.video_pll.clkouts[0].freq, 12000000)

    def test_pins_and_electrical_constraints(self):
        board, config = load_machine(SOFT, self.root)
        board['resources']['clock']['signals']['clk'].update(pins=['T1'], io_standard='LVCMOS25')
        board['input_clock_hz'] = 24000000
        platform = create_platform(bind_ports(from_data(board, config)), 'clk27', {})
        clock = platform.request('clk27')
        row = next(row for row in platform.constraint_manager.get_sig_constraints() if row[0] is clock)
        self.assertEqual(row[1], ['T1'])
        self.assertEqual(row[2][0].name, 'LVCMOS25')
        self.assertEqual(platform.default_clk_period, 1e9 / 24000000)

    def test_invalid_bindings(self):
        board, config = load_machine(HARD, self.root)
        config['devices']['audio'] = 'missing'
        with self.assertRaisesRegex(ValueError, 'audio resource'): from_data(board, config)

    def test_ddr_clock_must_match_ip(self):
        board, config = load_machine(HARD, self.root)
        config['ip']['ddr3']['parameters']['Memory_Clock'] = 300
        with self.assertRaisesRegex(ValueError, 'DDR UI clock'): from_data(board, config)


if __name__ == '__main__':
    unittest.main()
