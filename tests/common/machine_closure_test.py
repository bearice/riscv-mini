"""Each machine declares a closed source allowlist; imports must stay inside it.

Stage A moved every machine file into its own package. The build backends now
hash exactly the files declared in their closure, so a declared-but-missing
file fails the build and an import that reaches outside the declaration would
silently escape the build key. Both cases are checked here.
"""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from scripts.modular.machines.primer20k_vexriscv.hardware import PRIMER20K_CLOSURE, source_closure
from scripts.modular.machines.mega138k_ae350.ae350 import AE350_CLOSURE

MACHINES = {
    'primer20k_vexriscv': ({
        'gateware.machines.primer20k_vexriscv.soc',
        'gateware.machines.primer20k_vexriscv.rtl',
    }, PRIMER20K_CLOSURE),
    'mega138k_ae350': ({
        'gateware.machines.mega138k_ae350.soc',
        'gateware.machines.mega138k_ae350.platform',
    }, AE350_CLOSURE),
}


def module_path(name):
    relative = Path(*name.split('.'))
    candidate = ROOT / relative.with_suffix('.py')
    if candidate.is_file():
        return candidate
    candidate = ROOT / relative / '__init__.py'
    return candidate if candidate.is_file() else None


def package_parts(path):
    relative = path.relative_to(ROOT)
    return list(relative.parts[:-1])


def imported_modules(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    parts = package_parts(path)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = parts[:len(parts) - (node.level - 1)]
                if node.module:
                    names.add('.'.join(base + [node.module]))
                else:
                    for alias in node.names:
                        names.add('.'.join(base + [alias.name]))
            elif node.module:
                names.add(node.module)
                if node.module == 'gateware' or node.module.startswith('gateware.'):
                    for alias in node.names:
                        names.add(node.module + '.' + alias.name)
    return names


def walk_imports(entries):
    seen = set()
    paths = {}
    pending = list(entries)
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        path = module_path(name)
        if path is None:
            continue
        paths[path.relative_to(ROOT).as_posix()] = name
        for imported in imported_modules(path):
            if imported == 'gateware' or imported.startswith('gateware.'):
                pending.append(imported)
    return paths


class MachineClosureTests(unittest.TestCase):
    def test_declared_files_exist(self):
        self.assertEqual(len(source_closure(ROOT)), len(PRIMER20K_CLOSURE))
        for name in AE350_CLOSURE:
            self.assertTrue((ROOT / name).is_file(), name)

    def test_imports_stay_inside_the_declaration(self):
        for machine, (entries, closure) in MACHINES.items():
            declared = {name for name in closure
                        if name.startswith('gateware/') and name.endswith('.py')}
            reached = walk_imports(entries)
            for path in sorted(set(reached) - declared):
                self.fail('%s imports %s (reached via %s) but the closure does not declare it'
                          % (machine, path, reached[path]))

    def test_machine_closures_do_not_overlap(self):
        twentyk = set(PRIMER20K_CLOSURE)
        ae350 = set(AE350_CLOSURE)
        overlap = {name for name in twentyk & ae350
                   if name.startswith('gateware/machines/')}
        self.assertEqual(overlap, set())
        self.assertNotIn('gateware/boards/mega138k_pro/board.json', twentyk)
        self.assertNotIn('gateware/boards/primer20k_dock3713/board.json', ae350)
        self.assertFalse(any('mega138k_ae350' in name for name in twentyk))
        self.assertFalse(any('primer20k_vexriscv' in name for name in ae350))
        self.assertFalse(any('gowin_gw5ast' in name for name in twentyk))
        self.assertFalse(any('gowin_gw2a' in name for name in ae350))


if __name__ == '__main__':
    unittest.main()
