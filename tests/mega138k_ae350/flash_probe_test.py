"""A timing probe must stay distinct from a runnable board image."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from gateware.description import load_machine, from_data, validate
from mini_ops.mega138k_ae350.ae350 import candidate


class ProbeTests(unittest.TestCase):
    def test_probe_configuration_is_typed(self):
        board, machine = load_machine('mega138k_ae350', ROOT)
        machine = copy.deepcopy(machine)
        path = ROOT / 'gateware/machines/mega138k_ae350/machine.json'
        machine['rtl']['timing_probe'] = True
        validate(machine, 'machine', path)
        self.assertTrue(from_data(board, machine).rtl.timing_probe)
        machine['rtl']['timing_probe'] = 'true'
        with self.assertRaises(ValueError):
            validate(machine, 'machine', path)

    def test_probe_cannot_be_selected_for_board_operations(self):
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / 'result.json'
            result.write_text(json.dumps(dict(artifact=dict(component='gateware', digest='probe'))))
            with patch('mini_ops.mega138k_ae350.ae350.Store') as store:
                store.return_value.get.return_value = dict(config=dict(
                    machine='mega138k_ae350', synthesize=True, timing_probe=True))
                with self.assertRaisesRegex(ValueError, 'not a runnable'):
                    candidate(str(result))


if __name__ == '__main__':
    unittest.main()
