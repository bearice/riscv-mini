"""Compile each optional module on/off and check emitted CSR/pin ownership. No downloads."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from gateware.features import Features

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs',type=int,default=2)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'build/feature-matrix')
    parser.add_argument('--configuration-only',action='store_true',help='CPU x SD profiles plus audio clock combinations')
    args=parser.parse_args();directory=args.output_dir.resolve();directory.mkdir(parents=True,exist_ok=True)
    cases=[('full',[]),('minimal',['--profile','minimal']),('uart-only',['--profile','minimal','--without-flash'])]
    dependencies={'filesystem':['--with-sd'],'mic_stereo':['--with-mic']}
    for name in Features.names():
        flag=name.replace('_','-')
        cases += [(f'only-{name}',['--profile','minimal',f'--with-{flag}',*dependencies.get(name,[])])]
        cases += [(f'no-{name}',[f'--without-{flag}'])]
    cases += [('sd-spi',['--profile','minimal','--with-sd','--sd-backend','spi']),
              ('sd-spi-fs',['--profile','minimal','--with-sd','--with-filesystem','--sd-backend','spi'])]
    configuration_cases=[]
    for mmu in (False,True):
        for fpu in (False,True):
            for sd in ('none','spi','lite','full'):
                name=f'cpu-m{int(mmu)}-f{int(fpu)}-sd-{sd}'
                flags=[f'--{"with" if mmu else "without"}-mmu',f'--{"with" if fpu else "without"}-fpu','--sd-profile',sd]
                configuration_cases.append((name,flags))
    configuration_cases += [('audio-dds',['--audio-clock','dds']),
                            ('mic-dds-only',['--profile','minimal','--with-mic','--with-mic-stereo','--audio-clock','dds']),
                            ('audio-dds-unused',['--profile','minimal','--audio-clock','dds']),
                            ('audio-legacy',['--audio-clock','legacy'])]
    cases=configuration_cases if args.configuration_only else cases+configuration_cases
    expected={'flash':'flash_spi','spi_lcd':'lcd_spi','video':'rgb_lcd','board_io':'board_io',
              'ws2812':'ws2812','audio':'audio','mic':'mic','eth':'ethmac','usb':'usb_host'}
    def build(case):
        name,flags=case;output=directory/name;log=directory/(name+'.log')
        command=[sys.executable,str(ROOT/'scripts/build.py'),'--flat-verilog','--output-dir',str(output),*flags]
        with log.open('w',encoding='utf-8') as stream:
            status=subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT).returncode
        record={'name':name,'flags':flags,'passed':status==0,'log':str(log)}
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
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:records=list(pool.map(build,cases))
    report={'passed':all(r['passed'] for r in records),'cases':records,'board_test':'not performed'}
    (directory/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    if not report['passed']:raise SystemExit('Feature matrix failed; see result.json and per-case logs')
if __name__=='__main__':main()
