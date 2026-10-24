"""Vendor IP archives retain synthesis dependencies emitted inside the project."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.modular.ip.manager import generate
from scripts.modular.storage import read, write


class IPGenerationTests(unittest.TestCase):
    def test_gowin_project_support_sources_are_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe = root / 'recipe.json'
            output = root / 'generated/pll'
            write(recipe, dict(root=str(root), output=str(output),
                definition=dict(generator='gowin_pll', module='Example', parameters={}),
                tools={'gowin': ['unused']}, environment={}, source_identity={},
                tool_identity={}, board={}, build_directory=str(root / 'logs')))

            def native_generate(definition, board, stage, tool, environment):
                (stage / 'project/src').mkdir(parents=True)
                (stage / 'project/src/pll_init.v').write_text('module PLL_INIT; endmodule\n')
                primary = stage / 'Example.v'
                primary.write_text('module Example; PLL_INIT init(); endmodule\n')
                return primary

            with mock.patch('scripts.modular.ip.manager.generator_inputs', return_value={}), \
                 mock.patch('scripts.modular.ip.gowin.generate', side_effect=native_generate):
                self.assertEqual(generate(recipe), 0)
            manifest = read(output / 'ip-manifest.json')
            self.assertIn('project/src/pll_init.v', manifest['files'])
            self.assertTrue((output / 'project/src/pll_init.v').is_file())


if __name__ == '__main__':
    unittest.main()
