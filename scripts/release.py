"""Rebuild a committed clean checkout, then preserve a verified final artifact bundle."""
import argparse
import hashlib
import importlib.metadata
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from build_records import ROOT, artifact_identity, configuration_name, get_record, git, now, pin, register, source_identity, write_json
from versions import bundle_versions, read_versions


def release_version(value):
    value=value.strip().removeprefix('v')
    match=re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?',value)
    identifiers=match[4].split('.') if match and match[4] else []
    if not match or any(not token or token.isdigit() and len(token)>1 and token[0]=='0' for token in identifiers):
        raise ValueError('Use a semantic release version such as 0.7.0')
    return value


def verify_bundle(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory/'release.json').read_text(encoding='utf-8'))
    if manifest['state'] != 'complete':raise ValueError('Final bundle is not complete')
    for name, digest in manifest['files'].items():
        path = (directory/name).resolve()
        if not path.is_relative_to(directory) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Final bundle file differs: '+name)
    artifact_identity(directory)
    return manifest


def package(run, destination, source):
    """Select final outputs; implementation scratch files remain disposable."""
    v = artifact_identity(run)
    for relative in ('firmware','reports'):
        (destination/relative).mkdir(parents=True,exist_ok=True)
    (destination/'gateware').mkdir(exist_ok=True)
    shutil.copy2(run/'gateware/riscv_mini.fs',destination/'gateware/riscv_mini.fs')
    for p in (run/'firmware').iterdir():
        if p.suffix in ('.bin','.img','.elf','.map'):shutil.copy2(p,destination/'firmware'/p.name)
    shutil.copytree(Path(source['recipe']),destination/'replay')
    shutil.copy2(run/'build-info.json',destination/'reports/build-info.json')
    shutil.copy2(run/'validation.json',destination/'reports/validation.original.json')
    for p in run.glob('*.log'):shutil.copy2(p,destination/'reports'/p.name)
    for p in (run/'gateware/impl').rglob('*'):
        if p.is_file() and p.suffix in ('.html','.rpt','.log','.txt'):
            target=destination/'reports/pnr'/p.relative_to(run/'gateware/impl')
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
    for name in ('firmware-verification.json','firmware-verification-uart.log'):
        if (run/name).exists():shutil.copy2(run/name,destination/'reports'/name)
    for name in ('CHANGELOG.md','requirements.lock','VERSIONS.yaml','RELEASES.yaml'):
        if (ROOT/name).exists():shutil.copy2(ROOT/name,destination/name)
    if (ROOT/'reports').exists():shutil.copytree(ROOT/'reports',destination/'reports/commit-records')
    v['bitstream']=str(destination/'gateware/riscv_mini.fs')
    v['final_git_commit']=source['commit']
    v['release_version']=source['version']
    v['components']=source.get('components')
    write_json(destination/'validation.json',v)
    recipe=json.loads((destination/'replay/recipe.json').read_text(encoding='utf-8'))
    write_json(destination/'build-parameters.json',dict(arguments=recipe['arguments'],
              input_root='replay/', version=source['version'], source_commit=source['commit'], source_inputs_sha256=source['inputs_sha256'],
              application=v.get('application_source'), original_run=str(run)))
    tools=json.loads((ROOT/'.tools.local.json').read_text(encoding='utf-8'))
    tool_records={name:dict(path=value,sha256=hashlib.sha256(Path(value).read_bytes()).hexdigest() if Path(value).is_file() else None)
                  for name,value in tools.items()}
    write_json(destination/'toolchain.json',dict(python=sys.version,tools=tool_records,
               distributions=sorted((d.metadata['Name'],d.version) for d in importlib.metadata.distributions() if d.metadata['Name'])))
    return v


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build', help='Explicit catalog template (configuration/input CPU RTL), e.g. current')
    p.add_argument('--version',help='System bundle version to release; must be pinned in RELEASES.yaml and tagged system/v<version>')
    p.add_argument('--snapshot',action='store_true',help='Archive a code commit without creating a new code release; retain version plus Git hash')
    p.add_argument('--app',type=Path,help='Explicit application for legacy templates without application_source')
    p.add_argument('--board-check',action='store_true',help='SRAM/UART only; run firmware checks and 60 s soak')
    p.add_argument('--baseline',help='Explicit baseline ID/reference; enables final all/cache measurements')
    p.add_argument('--baseline-results',type=Path,action='append',default=[])
    p.add_argument('--allow-legacy-results',action='store_true')
    p.add_argument('--verify',type=Path,help='Read-only verify an existing final bundle')
    a=p.parse_args()
    if a.verify:
        print(json.dumps(verify_bundle(a.verify),ensure_ascii=False,indent=2));return
    if not a.build:p.error('--build required')
    if a.baseline and (not a.board_check or not a.baseline_results):p.error('Performance requires --board-check and explicit baseline results')
    if git('status','--porcelain'):p.error('Commit code first; final artifacts require a clean checkout')
    if not a.version:p.error('--version required: the system bundle version to release; pin it in RELEASES.yaml first')
    version=release_version(a.version)
    if a.snapshot:
        # A commit snapshot is a whole-tree archive, not a qualified bundle: it
        # keeps the legacy v<version> label and needs no RELEASES.yaml entry.
        components=None;tag='v'+version
    else:
        components=bundle_versions(version);tag='system/v'+version
        try:tag_commit=git('rev-parse',tag+'^{commit}').decode().strip()
        except subprocess.CalledProcessError:p.error('Create the local release tag '+tag+' after committing code first')
        if tag_commit!=git('rev-parse','HEAD').decode().strip():p.error('Release tag must point to the clean checkout being built')
    template=get_record(a.build);v=artifact_identity(template['path'])
    source=source_identity();source.update(kind='captured',created_at=now(),version=version,components=components)
    config=configuration_name(v['profile'],v['isa'],v['rom_size_bytes'],v['l2_size_bytes'])
    final=(ROOT/'build/archives'/f"{tag}-{source['commit'][:12]}" if a.snapshot else ROOT/'build/releases'/tag)/config
    receipt=dict(version=version,tag=None if a.snapshot else tag,release_kind='commit-snapshot' if a.snapshot else 'code-release')
    if final.exists():p.error('Final bundle exists; verify it instead of overwriting it')
    final.mkdir(parents=True)
    write_json(final/'release.json',dict(state='building',**receipt,commit=source['commit'],created_at=now()))
    inputs=final/'inputs/cpu';inputs.mkdir(parents=True)
    cpu=Path(v['cpu_verilog'])
    for f in (cpu,cpu.with_suffix('.yaml'),cpu.parent/'generator.json'):
        if f.is_file():shutil.copy2(f,inputs/f.name)
    app=a.app or Path(v.get('application_source',''))
    if not app.is_file():p.error('Select --app explicitly for this template')
    run=ROOT/'build/runs'/f"{tag}-{source['commit'][:12]}-{config}-final-build"
    if run.exists():p.error('Final build run already exists; preserve it for diagnosis')
    args=['--profile',v['profile'],'--purpose','final-build','--app',str(app.resolve()),
          '--cpu-variant',v['cpu_variant'],'--cpu-verilog',str(inputs/cpu.name),
          '--rom-size',str(v['rom_size_bytes']),'--l2-size',str(v['l2_size_bytes']),
          '--sd-profile',v['sd_profile'],'--usb-backend',v['usb_backend'],'--audio-clock',v['audio_clock'],
          '--place-option',str(v['place_option']),'--route-option',str(v['route_option']),
          *['--'+('with-' if enabled else 'without-')+name.replace('_','-') for name,enabled in v['features'].items()],
          '--synthesize','--output-dir',str(run)]
    if v.get('verilog_mode')=='flat':args.append('--flat-verilog')
    if v.get('rtl_hierarchy')=='deep':args.append('--deep-verilog')
    write_json(final/'invocation.json',dict(commit=source['commit'],command=[sys.executable,'scripts/build.py',*args]))
    def execute(name,command):
        print('Running',name,flush=True)
        with (final/(name+'.log')).open('w',encoding='utf-8') as log:
            result=subprocess.run([sys.executable,'-X','utf8',*command],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:raise RuntimeError(f'{name} failed; see {final/(name+".log")}')
    try:
        execute('build',['scripts/build.py',*args])
        info=json.loads((run/'build-info.json').read_text(encoding='utf-8'));source=info['source']
        source['version']=version
        source['components']=components
        if source['dirty'] or source['commit']!=git('rev-parse','HEAD').decode().strip():raise ValueError('Committed source changed during final build')
        if a.board_check:execute('board',['scripts/firmware_verify.py','--program','--output-dir',str(run),'--soak-seconds','60','--mic'])
        if a.baseline:
            for suite in ('all','cache'):
                execute('benchmark-'+suite,['scripts/benchmark.py','--build-dir',str(run),'--suite',suite,'--rounds','3',
                                            '--output-dir',str(final/'reports/performance/candidate'/suite)])
            baseline_dirs=[]
            for i,path in enumerate(a.baseline_results):
                target=final/'reports/performance/baseline'/f'{i}-{path.name}'
                shutil.copytree(path,target);baseline_dirs.append(target)
            record=register(run,'final-build',source)
            command=['scripts/performance_report.py','--candidate',record['id'],'--baseline',a.baseline,
                     '--candidate-results',str(final/'reports/performance/candidate/all'),
                     '--candidate-results',str(final/'reports/performance/candidate/cache'),
                     *[x for path in baseline_dirs for x in ('--baseline-results',str(path))],
                     '--output',str(final/'reports/performance/comparison.md')]
            command.extend(['--limitation','Comparison includes the complete configuration change; results cannot isolate C or fence removal.',
                            '--limitation','External network throughput and audible output are not validated by these all/cache runs.'])
            if a.allow_legacy_results:command.append('--allow-legacy-results')
            execute('performance-report',command)
        package(run,final,source)
        # The final replay recipe must be self-contained, not tied to the disposable run.
        final_source=dict(source,recipe=str(final/'replay'))
        record=register(final,'commit-snapshot' if a.snapshot else 'code-release',final_source,label='提交后快照' if a.snapshot else '正式代码发布')
        files={str(f.relative_to(final)).replace('\\','/'):hashlib.sha256(f.read_bytes()).hexdigest()
               for f in final.rglob('*') if f.is_file() and f.name!='release.json'}
        write_json(final/'release.json',dict(state='complete',**receipt,commit=source['commit'],source_inputs_sha256=source['inputs_sha256'],
                   build_id=record['id'],created_at=now(),board_checked=a.board_check,files=files))
        verify_bundle(final)
        if git('status','--porcelain') or source_identity()['inputs_sha256']!=source['inputs_sha256']:
            raise ValueError('Worktree changed during finalization')
        if a.board_check:pin('current',record['id'],final/'reports/firmware-verification.json')
        print('Final artifact:',final,flush=True)
    except Exception as error:
        write_json(final/'release.json',dict(state='failed',**receipt,commit=source['commit'],error=str(error),created_at=now()))
        raise


if __name__=='__main__':main()
