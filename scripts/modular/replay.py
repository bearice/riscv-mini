"""Strict source replay: validate before execution, never apply current defaults."""
import shutil
import subprocess
import sys
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[2] / 'scripts')]
from scripts.modular.storage import canonical, digest, read, write, workspace_lock
from scripts.modular import machines as registry
from scripts.modular.machines import LEGACY, system_machine


def locked_build(store, path, run, plan=False):
    data = read(path)
    ref = data.get('system')
    if not ref:
        # A system store lock can be used when its exact dependency artifacts exist locally.
        if data.get('schema') != 1 or not data.get('components'):
            raise ValueError('Unknown/incomplete system lock')
        for item in data['components'].values(): store.get(item, recursive=True)
        registry.part(LEGACY, 'contracts').assembly(store, data['components'])
        ref = store.publish('system', digest(data), {'bundle.lock.json':canonical(data)},
                            config=dict(configuration=data['configuration']),dependencies=data['components'])
    elif (Path(path).parent / 'artifacts').is_dir():
        ref = registry.part(LEGACY, 'system').import_export(store, Path(path).parent)
    else:
        store.get(ref, recursive=True)
    machine = system_machine(store, ref)
    registry.part(machine, 'contracts').assembly(store, read(store.path(ref) / 'bundle.lock.json')['components'])
    if plan:
        run.step('system','REUSE',ref,reason='exact locked artifact closure verified')
        return ref
    return rebuild(store, ref, run)


def rebuild(store, ref, run):
    """Rebuild firmware from its recorded source snapshot; retain fixed hardware."""
    machine = system_machine(store, ref)
    lock = read(store.path(ref) / 'bundle.lock.json')
    parts = dict(lock['components'])
    for name in ('hal-sdk','bios'):
        if name not in parts: raise ValueError('Legacy bundle has no rebuildable HAL SDK; build a modular candidate first')
        manifest = store.get(parts[name])
        if not manifest['config'].get('build'):
            raise ValueError('Legacy firmware lacks a complete source/flag lock: ' + name)
        expected = manifest['config']['build']
        # Hash the current implementation and its imports before allowing it to execute.
        from scripts.modular.compile import source_files
        actual = source_files(store.root, list(expected['sources']))
        if actual != expected['sources']:
            raise ValueError('Source differs from lock for ' + name + '; use the preserved inputs/source snapshot in its recorded checkout')
        if run.result.get('plan'): continue
        rebuilt = registry.part(machine, 'firmware').build_component(store, run, name, parts['soc-description'],
                                  boot=parts.get('bootloader') if name=='bios' else None,
                                  sdk=parts.get('hal-sdk') if name=='bios' else None,
                                  expected=expected, force=True)
        if rebuilt != parts[name]: raise ValueError('Rebuild output differs from locked artifact: ' + name)
    run.result['hardware_replay'] = 'exact preserved artifacts; vendor synthesis not rerun'
    return ref


def rebuild_preserved(store, ref, run):
    """Each component executes its own preserved implementation in a separate checkout."""
    import tempfile
    from scripts.modular.storage import Store
    export_bundle = registry.part(system_machine(store, ref), 'system').export_bundle
    from scripts.tools.versions import COMPONENTS
    lock=read(store.path(ref)/'bundle.lock.json')
    parts=lock['components']
    for name in ('hal-sdk','bios'):
        if name not in parts:raise ValueError('Legacy firmware is not source-rebuildable; build a modular candidate first')
        artifact=store.path(parts[name]);manifest=store.get(parts[name])
        if not manifest['config'].get('build'):raise ValueError('Missing source/tool lock for '+name)
        parent=store.build/'work/replays';parent.mkdir(parents=True,exist_ok=True)
        checkout=Path(tempfile.mkdtemp(prefix=name+'-',dir=parent))
        # Component snapshots contain the build implementation and all declared source inputs.
        for source in (artifact/'inputs/source').rglob('*'):
            if source.is_file():
                target=checkout/source.relative_to(artifact/'inputs/source')
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        values={c:'0.0.0' for c in COMPONENTS}
        for component,item in parts.items():
            role={'hal-sdk':'hal','gateware':'rtl'}.get(component,component)
            if role in values:values[role]=store.get(item)['version'] or '0.0.0'
        (checkout/'VERSIONS.yaml').write_text(''.join(c+': '+values[c]+'\n' for c in COMPONENTS),encoding='utf-8')
        if (store.root/'.tools.local.json').is_file():shutil.copyfile(store.root/'.tools.local.json',checkout/'.tools.local.json')
        package=checkout/'build/input-package'
        export_bundle(store,ref,package)
        entrypoint=manifest['config']['build'].get('rebuild_entrypoint',['scripts/modular_build.py','_rebuild-one'])
        command=[sys.executable,str(checkout/entrypoint[0]),*entrypoint[1:],str(package),name]
        with (run.path/(name+'-replay.log')).open('w',encoding='utf-8') as stream:
            result=subprocess.run(command,cwd=checkout,stdout=stream,stderr=subprocess.STDOUT)
        if result.returncode:raise ValueError('Preserved '+name+' replay failed; see '+str(run.path/(name+'-replay.log')))
        replayed=Store(checkout)
        replayed.get(parts[name],recursive=True)
        run.step(name,'REBUILD',parts[name],reason='preserved source, flags and implementation; output digest matched')
    run.result['hardware_replay']='exact preserved CPU/bitstream; vendor synthesis not rerun'
    return ref
