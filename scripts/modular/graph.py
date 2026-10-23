"""Write Ninja actions around existing generators and LiteX build operations.

Ninja owns graph checks and scheduling. This module adds no resource manager,
artifact cache or independent execution engine.
"""
from dataclasses import dataclass, field
from pathlib import Path
import sys

from scripts.modular.compile import ninja_path, quote_command
from scripts.modular.storage import canonical, source_file_hash, text_file


@dataclass(frozen=True)
class Action:
    name: str
    argv: tuple
    inputs: tuple
    outputs: tuple
    dependencies: tuple = ()
    parameters: dict = field(default_factory=dict)
    pool: str = 'default'


class Graph:
    def __init__(self, root, work):
        self.root, self.work = Path(root), Path(work)
        self.actions = {}

    def add(self, action):
        if action.name in self.actions:
            raise ValueError('Duplicate action: ' + action.name)
        if not action.outputs:
            raise ValueError('Action has no outputs: ' + action.name)
        self.actions[action.name] = action

    def compile(self, targets):
        order = list(self.actions.values())
        unknown = set(targets) - self.actions.keys()
        if unknown:
            raise ValueError('Unknown graph targets: ' + ', '.join(sorted(unknown)))
        self.work.mkdir(parents=True, exist_ok=True)
        worker = self.root / 'scripts/mini.py'
        requests = {}
        lines = ['ninja_required_version = 1.10', 'builddir = ' + ninja_path(self.work), 'pool vendor', '  depth = 1',
                 'pool component', '  depth = 1', 'rule component',
                 '  command = ' + quote_command([sys.executable, worker, '_worker', 'action']) + ' $request',
                 '  description = $label', '  restat = 1']
        for action in order:
            request = self.work / 'actions' / (action.name + '.json')
            source_hashes = {str(Path(path).resolve()): source_file_hash(path)
                             for path in action.inputs if Path(path).is_file() and
                             not any(Path(path).resolve() == Path(out).resolve()
                                     for producer in order for out in producer.outputs)}
            # This request is an input projection, not another artifact cache.
            # Existing action workers and store retain their established roles.
            record = dict(argv=list(map(str, action.argv)), cwd=str(self.root), environment={},
                          parameters=action.parameters, input_identity=source_hashes)
            text_file(request, canonical(record).decode() + '\n')
            inputs = [request, *action.inputs,
                      *[output for name in action.dependencies for output in self.actions[name].outputs],
                      worker, Path(__file__)]
            lines.append('build ' + ' '.join(ninja_path(path) for path in action.outputs) +
                         ': component ' + ' '.join(ninja_path(path) for path in inputs))
            lines += ['  request = ' + quote_command([request]).replace('$', '$$'),
                      '  label = ' + action.name]
            if action.pool != 'default':
                if action.pool not in ('vendor', 'component'):
                    raise ValueError('Unknown action pool: ' + action.pool)
                lines.append('  pool = ' + action.pool)
            requests[action.name] = request
        lines.append('default ' + ' '.join(ninja_path(output) for name in targets for output in self.actions[name].outputs))
        path = self.work / 'build.ninja'
        text_file(path, '\n'.join(lines) + '\n')
        return path
