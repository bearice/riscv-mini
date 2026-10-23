"""Render native Gowin Tcl/IPC inputs from closed machine IP parameters."""
from pathlib import Path
import subprocess
from scripts.modular.storage import text_file
from scripts.modular.environment import process_environment

IP_IDS = {'gowin_ae350': 'riscv_ae350_soc', 'gowin_ddr3': 'ddr3', 'gowin_pll': 'clock_plladv'}


def word(value):
    value = str(value)
    if any(ch in value for ch in ('\n', '\r', '\x00')):
        raise ValueError('Multiline Tcl value is not supported')
    return ''.join('\\' + char if char.isspace() or char in '\\$[]{};"' else char for char in value) or '{}'


def generate(definition, board, destination, tool, environment):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    module = definition['module']
    if definition['generator'] == 'gowin_ae350':
        from scripts.modular.ip.ae350 import generate as generate_ae350
        home = environment.get('GOWIN_ROOT') or environment.get('GOWIN_HOME')
        ide = Path(home) / 'IDE' if home else Path(tool[0]).resolve().parents[1]
        return generate_ae350(definition, destination, ide)
    properties = []
    for key, value in sorted(definition['parameters'].items()):
        if type(value) is bool:
            value = str(value).lower()
        properties.extend(('CONFIG.' + key, value))
    lines = ['create_project -name ip_generation -dir %s -pn %s -device_version %s -force' %
             (word((destination / 'project').as_posix()), word(board['fpga']['part']),
              word(board['fpga']['device'][-1])),
             'set_device -name %s %s' % (word(board['fpga']['device']), word(board['fpga']['part'])),
             'create_ipc -name %s -language Verilog -module_name %s -file_name %s -dir %s -force' %
             (word(IP_IDS[definition['generator']]), word(module), word(module), word(destination.as_posix())),
             'set_property -dict [list %s] [get_ips %s]' %
             (' '.join(word(value) for value in properties), word(module)),
             'report_property [get_ips %s]' % word(module),
             'generate_target [get_ips %s]' % word(module)]
    script = destination / 'generate.tcl'
    text_file(script, '\n'.join(lines) + '\n')
    from scripts.modular.gowin import run_with_license_retry
    import shutil
    def attempt(log_path):
        with log_path.open('w') as log:
            return subprocess.run([*tool, str(script)], cwd=destination,
                env=process_environment(environment), stdout=log, stderr=subprocess.STDOUT).returncode
    code, final_log = run_with_license_retry(attempt, destination / 'logs', prefix='ip')
    shutil.copyfile(final_log, destination / 'generate.log')
    if code:
        raise ValueError('Gowin IP generation failed; inspect generate.log and license attempt records')
    import re
    declaration = re.compile(r'\bmodule\s+' + re.escape(module) + r'\b')
    primary = [path for path in destination.rglob('*.v')
               if declaration.search(path.read_text(encoding='utf-8', errors='replace'))]
    if len(primary) != 1:
        raise ValueError('Gowin did not produce one primary module: ' + module)
    from configparser import ConfigParser
    configurations = []
    for path in destination.rglob('*.ipc'):
        config = ConfigParser(interpolation=None)
        config.optionxform = str
        config.read(path, encoding='utf-8-sig')
        if config.get('General', 'module', fallback=None) == module:
            configurations.append(config)
    if len(configurations) != 1:
        raise ValueError('Generated Gowin IP must retain one matching IPC configuration')
    config = configurations[0]
    report = (destination / 'generate.log').read_text(encoding='utf-8', errors='replace')
    for key, expected in definition['parameters'].items():
        requested = expected
        expected = str(expected).lower() if type(expected) is bool else str(expected)
        matches = re.findall(r'^CONFIG\.' + re.escape(key) + r'\s+\{([^}]*)\}', report, re.M)
        from decimal import Decimal, InvalidOperation
        same = bool(matches) and matches[-1] == expected
        if matches and type(requested) in (int, float):
            try: same = Decimal(matches[-1]) == Decimal(expected)
            except InvalidOperation: same = False
        if not same:
            raise ValueError('Gowin did not apply native property: ' + key)
    return primary[0]
