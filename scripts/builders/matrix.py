"""Compile each optional module on/off and check emitted CSR/pin ownership. No downloads."""
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from gateware.machines.primer20k_vexriscv.features import Features


def cases(configuration_only=False):
    """Case data describes source configurations, never command-line overrides."""
    result = [('full', 'full', {}, {}), ('minimal', 'minimal', {}, {}),
              ('uart-only', 'minimal', {'flash': False}, {})]
    dependencies = {'filesystem': {'sd': True}, 'mic_stereo': {'mic': True},
                    'eth_dma': {'eth': True, 'flash': True}, 'eth': {'flash': True}}
    for name in Features.names():
        result += [('only-' + name, 'minimal', {**dependencies.get(name, {}), name: True}, {}),
                   ('no-' + name, 'full', {name: False}, {})]
    result += [('sd-spi', 'minimal', {'sd': True}, {'sd_profile': 'spi'}),
               ('sd-spi-fs', 'minimal', {'sd': True, 'filesystem': True}, {'sd_profile': 'spi'})]
    configurations = [('cpu-m%d-f%d-sd-%s' % (mmu, fpu, sd), 'full',
                       dict(mmu=mmu, fpu=fpu, sd=sd != 'none', filesystem=sd != 'none'),
                       dict(sd_profile=sd))
                      for mmu in (False, True) for fpu in (False, True)
                      for sd in ('none', 'spi', 'lite', 'full')]
    configurations += [('audio-dds', 'full', {}, {'audio_clock': 'dds'}),
                       ('mic-dds-only', 'minimal', {'mic': True, 'mic_stereo': True}, {'audio_clock': 'dds'}),
                       ('audio-dds-unused', 'minimal', {}, {'audio_clock': 'dds'}),
                       ('audio-legacy', 'full', {}, {'audio_clock': 'legacy'})]
    return configurations if configuration_only else result + configurations


def configuration(base, case):
    import copy
    _, profile, overrides, settings = case
    value = copy.deepcopy(base)
    features = Features.resolve(profile, overrides).as_dict()
    value['ip']['cpu']['parameters'].update({key: features.pop(key) for key in ('mmu', 'fpu')})
    value['rtl'].update(features=features, sd_profile='lite' if features['sd'] else 'none',
                        rtl_hierarchy='flat', **{key: val for key, val in settings.items() if key != 'sd_profile'})
    if 'sd_profile' in settings: value['rtl']['sd_profile'] = settings['sd_profile']
    return value


def build(args, run=None):
    from gateware.description import load_machine, validate, validate_pair
    from scripts.modular.storage import write, workspace_lock
    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    board, base = load_machine('primer20k_vexriscv', ROOT)
    config_path = ROOT / 'gateware/machines/primer20k_vexriscv/machine.json'
    expected={'flash':'flash_spi','spi_lcd':'lcd_spi','video':'rgb_lcd','board_io':'board_io',
              'ws2812':'ws2812','audio':'audio','mic':'mic','eth':'ethmac','usb':'usb_host'}
    def build(case):
        name = case[0]
        output, log = directory / name, directory / (name + '.log')
        selected = configuration(base, case)
        validate(selected, 'machine', config_path)
        validate_pair(board, selected)
        write(config_path, selected)
        command = [sys.executable, str(ROOT / 'scripts/mini.py'), 'build', 'system',
                   '--machine', selected['id'], '--workspace', getattr(args, 'workspace', 'default'),
                   '--purpose', 'matrix-' + name, '--output-dir', str(output)]
        with log.open('w',encoding='utf-8') as stream:
            status=subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT).returncode
        record={'name':name,'configuration':selected,'passed':status==0,'log':str(log)}
        if status==0:
            validation=json.loads((output/'validation.json').read_text());csr=json.loads((output/'csr.json').read_text())
            features=validation['features'];bases=csr['csr_bases'];rtl=(output/'gateware/riscv_mini.v').read_text()
            for feature,base in expected.items():assert (base in bases)==features[feature],(name,feature,bases)
            assert ('usb_ulpi_clk' in rtl)==features['usb']
            assert ('rgb_lcd_clk' in rtl)==features['video']
            assert ('microphone_second_data' in rtl)==features['mic_stereo']
            assert ('board_leds' in rtl)==features['board_io']
            assert ('spiflash_clk' in rtl)==features['flash']
            assert ('spisdcard' in bases)==(validation['sd_profile']=='spi')
            assert ('sdcard' in bases)==(validation['sd_profile'] in ('lite','full'))
            assert csr['constants']['mini_feature_mmu']==int(features['mmu'])
            assert csr['constants']['mini_feature_fpu']==int(features['fpu'])
            if validation['sd_profile'] in ('lite','full'):
                assert csr['csr_registers']['sdcard_mem2block_dma_base']['size']==(1 if validation['sd_profile']=='lite' else 2)
            dds_active=validation['audio_clock']=='dds' and (features['audio'] or features['mic'])
            assert csr['constants']['config_audio_dds']==int(dds_active)
            assert rtl.count('\nrPLL ')==validation['expected_plls'],(name,validation['expected_plls'])
            record.update(features=features,firmware_sizes=validation['firmware_sizes'],abi=validation['boot_image']['abi_tag'])
        print(name,'PASS' if record['passed'] else 'FAIL',flush=True);return record
    original = config_path.read_bytes()
    with workspace_lock(ROOT / 'build/work/.matrix-configuration'):
        try:
            records = [build(case) for case in cases(args.configuration_only)]
        finally:
            config_path.write_bytes(original)
    report={'passed':all(r['passed'] for r in records),'cases':records,'board_test':'not performed'}
    (directory/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    if not report['passed']:raise SystemExit('Feature matrix failed; see result.json and per-case logs')
