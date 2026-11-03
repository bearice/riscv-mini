"""The U-Boot installer wires the AE350 board and drivers into upstream files idempotently."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('uboot_install', ROOT/'firmware/uboot/install.py')
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)

ARCH_KCONFIG = '''choice
\tprompt "Target select"

config TARGET_A
\tbool "A"

endchoice

source "board/a/a/Kconfig"
source "board/z/z/Kconfig"

choice
\tprompt "Other"
endchoice
'''


class UbootInstallTests(unittest.TestCase):
    def tree(self, temp):
        src = Path(temp)
        (src/'arch/riscv').mkdir(parents=True)
        (src/'arch/riscv/Kconfig').write_text(ARCH_KCONFIG)
        for directory in ('mmc', 'net'):
            (src/'drivers'/directory).mkdir(parents=True)
            (src/'drivers'/directory/'Kconfig').write_text('menu "x"\nendmenu\n')
            (src/'drivers'/directory/'Makefile').write_text('obj-y += core.o\n')
        return src

    def test_board_hook_and_drivers_are_added_once(self):
        machine = install.MACHINES['mega138k_ae350']
        with tempfile.TemporaryDirectory() as temp:
            src = self.tree(temp)
            for _ in range(2):
                install.hook_board(src, machine['board'], *machine['target'])
                for driver in machine['drivers']:
                    install.add_driver(src, *driver)
            kconfig = (src/'arch/riscv/Kconfig').read_text()
            self.assertEqual(kconfig.count('config TARGET_MEGA138K_AE350\n'), 1)
            self.assertLess(kconfig.index('TARGET_MEGA138K_AE350'), kconfig.index('endchoice'))
            self.assertIn('source "board/z/z/Kconfig"\nsource "board/riscv-mini/mega138k_ae350/Kconfig"\n', kconfig)
            for directory, name, config, *_ in machine['drivers']:
                self.assertTrue((src/'drivers'/directory/(name+'.c')).is_file())
                self.assertEqual((src/'drivers'/directory/'Kconfig').read_text().count('config %s\n' % config), 1)
                self.assertEqual((src/'drivers'/directory/'Makefile').read_text().count(
                    'obj-$(CONFIG_%s) += %s.o\n' % (config, name)), 1)

    def test_missing_upstream_anchor_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            src = self.tree(temp)
            (src/'arch/riscv/Kconfig').write_text('source "board/a/a/Kconfig"\n')
            with self.assertRaisesRegex(RuntimeError, 'target choice'):
                install.hook_board(src, 'riscv-mini/x', 'TARGET_X', 'X')

    def test_inputs_are_the_tracked_machine_files(self):
        names = {p.relative_to(ROOT).as_posix() for p in install.inputs('mega138k_ae350')}
        self.assertIn('firmware/uboot/drivers/mmc/ae350_sd.c', names)
        self.assertIn('firmware/uboot/ae350/patches/10-single-hart-no-atomics.patch', names)
        self.assertNotIn('firmware/uboot/drivers/mmc/litesd.c', names)


if __name__ == '__main__':
    unittest.main()
