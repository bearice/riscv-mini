"""Board operation receipts shared by every machine's workflows."""
from datetime import datetime, timezone

import sys
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[2] / 'scripts')]
from scripts.tools.build_records import ROOT, write_json


class Operation:
    def __init__(self, command, build=None, root=ROOT):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        self.path = root/'build/runs'/f'{stamp}-board-{command}'
        self.path.mkdir(parents=True)
        self.report = dict(kind='test' if command=='verify' else 'board', operation=command, passed=False, steps=[],
                           selected_build=build.describe() if build else None,
                           device_identity='unconfirmed; selected files are not device readback')
        if build and build.validation.get('modular_system') and build.image_sha256==build.validation['boot_image']['sha256']:
            self.report['system']=build.validation['modular_system']
        self.save()

    def save(self):
        write_json(self.path/'result.json', self.report)

    def record(self, name, **values):
        self.report['steps'].append(dict(name=name, **values))
        self.save()
