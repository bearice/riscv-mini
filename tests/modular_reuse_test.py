"""Real target-toolchain acceptance for BIOS-only rebuilds and portable source replay.

Uses a disposable source snapshot and imported artifacts; never edits the checkout,
programs the board, or changes current. Invoke after a modular BIOS candidate exists.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from scripts.modular.storage import Store,Run,read,write
from scripts.modular.system import export_bundle,import_export,build_firmware_system
from scripts.modular.replay import rebuild


def main():
    store=Store(ROOT)
    candidates=sorted((store.build/'refs/candidates').glob('*.json'),reverse=True)
    base=None
    for candidate in candidates:
        ref=read(candidate)['system'];lock=read(store.path(ref)/'bundle.lock.json')
        if 'hal-sdk' in lock['components']:
            base=ref;break
    if not base:raise ValueError('First run mini.py build bios --hardware current')
    test=Run('test','modular-reuse',dict(system=base))
    test.result['system']=base
    print('Test run:',test.path,flush=True)
    sandbox=Path(tempfile.mkdtemp(prefix='modular-reuse-',dir=store.build/'work'))
    try:
        export_bundle(store,base,sandbox/'package')
        isolated=Store(sandbox/'checkout');isolated.root.mkdir()
        imported=import_export(isolated,sandbox/'package')
        lock=read(isolated.path(imported)/'bundle.lock.json');parts=lock['components']
        for ref in (parts['hal-sdk'],parts['bios']):
            artifact=isolated.path(ref)
            for source in (artifact/'inputs/source').rglob('*'):
                if source.is_file():
                    target=isolated.root/source.relative_to(artifact/'inputs/source')
                    target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        # Tool paths are local bindings; the lock contains their identities separately.
        shutil.copyfile(ROOT/'.tools.local.json',isolated.root/'.tools.local.json')
        import yaml
        from scripts.versions import COMPONENTS
        versions={name:'0.0.0' for name in COMPONENTS}
        for name,ref in parts.items():
            role={'hal-sdk':'hal','gateware':'rtl'}.get(name,name)
            if role in versions:versions[role]=isolated.get(ref)['version'] or '0.0.0'
        (isolated.root/'VERSIONS.yaml').write_text(yaml.safe_dump(versions),encoding='utf-8')
        replay=Run('test','source-replay',root=isolated.root)
        rebuilt=rebuild(isolated,imported,replay)
        assert rebuilt==base,'Unchanged source replay changed system'
        replay.finish(system=rebuilt)
        test.result['unchanged_source_replay']=True
        from scripts.modular.firmware import build_component
        warm=Run('test','incremental-baseline',root=isolated.root)
        warmed=build_component(isolated,warm,'bios',parts['soc-description'],boot=parts['bootloader'],sdk=parts['hal-sdk'],
                               force=True,fresh_workspace=False)
        assert warmed==parts['bios'],'Incremental workspace baseline differs'
        # Change BIOS implementation content in the isolated checkout only.
        source=isolated.root/'firmware/bios/console.c'
        source.write_bytes(source.read_bytes()+b'\n/* modular reuse acceptance: BIOS-only source edit */\n')
        changed=Run('build','bios-only',root=isolated.root)
        result=build_firmware_system(isolated,imported,'bios',changed)
        newer=read(isolated.path(result)/'bundle.lock.json')['components']
        for name in ('cpu','soc-description','bootloader','gateware','hal-sdk'):
            assert newer[name]==parts[name],name+' unexpectedly rebuilt'
        assert newer['bios']!=parts['bios'],'BIOS source change did not change artifact identity'
        assert not (changed.path/'hal-ninja.log').exists(),'HAL compiler ran for BIOS-only change'
        assert not (changed.path/'gateware.log').exists(),'Gowin ran for BIOS-only change'
        log=(changed.path/'app-ninja.log').read_text(encoding='utf-8')
        compiled=sum('] CC ' in line for line in log.splitlines())
        assert compiled==2,'Expected only changed console.c and identity-bearing main.c, got '+str(compiled)
        second=Run('build','unchanged',root=isolated.root)
        again=build_firmware_system(isolated,result,'bios',second)
        assert again==result,'No-op build changed system'
        assert all(step['status']=='REUSE' for step in second.result['steps']),'No-op build executed a component'
        test.finish(system=base,unchanged_source_replay=True,bios_only_reuse=True,no_op_reuse=True,
                    changed_system=result,hardware_unchanged=True,incremental_compiled_objects=compiled,sandbox=str(sandbox))
        print('PASS: portable source replay, BIOS-only rebuild, hardware/SDK reuse, no-op reuse')
    except BaseException as error:
        test.result['error']=str(error);test.save();raise


if __name__=='__main__':main()
