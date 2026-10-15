"""Build current sources using an explicit, preserved hardware configuration."""
import json
import subprocess
import sys
from pathlib import Path

from build_records import ROOT, run_path
from mini_ops.artifacts import Build
from mini_ops.board import Operation


def configuration_arguments(v, app, cpu, purpose, synthesize=False):
    """Shared by development builds and final release builds."""
    command=['--profile',v['profile'],'--purpose',purpose,'--app',str(Path(app).resolve()),
             '--cpu-variant',v['cpu_variant']]
    if cpu is not None:
        command += ['--cpu-verilog',str(Path(cpu).resolve())]
    command += [
             '--boot-mode',v.get('boot_mode','rom'),
             '--rom-size',str(v['rom_size_bytes'] or 4096),'--l2-size',str(v['l2_size_bytes']),
             '--sd-profile',v['sd_profile'],'--usb-backend',v['usb_backend'],'--audio-clock',v['audio_clock'],
             '--place-option',str(v['place_option']),'--route-option',str(v['route_option']),
             *['--'+('with-' if enabled else 'without-')+name.replace('_','-')
               for name,enabled in v['features'].items()]]
    if v.get('verilog_mode')=='flat':command.append('--flat-verilog')
    if v.get('rtl_hierarchy')=='deep':command.append('--deep-verilog')
    if synthesize:command.append('--synthesize')
    return command


def invocation(args):
    if args.minimal:
        command=['--profile','minimal','--purpose',args.purpose]
        if args.app:command+=['--app',str(args.app.resolve())]
    else:
        template=Build(args.template)
        v=dict(template.validation)
        if args.l2_size is not None:v['l2_size_bytes']=args.l2_size
        if args.place_option is not None:v['place_option']=args.place_option
        if args.route_option is not None:v['route_option']=args.route_option
        if args.boot_mode is not None:
            v['boot_mode']=args.boot_mode
            if args.boot_mode=='rom' and not v['rom_size_bytes']:v['rom_size_bytes']=8192
        app=args.app or Path(v.get('application_source',''))
        if not app.is_file():raise ValueError('Template application source missing; select --app')
        cpu_path=v.get('cpu_verilog')
        cpu=Path(cpu_path) if cpu_path else None
        if cpu is not None and not cpu.is_file():raise ValueError('Template CPU RTL missing; reproduce the template first')
        command=configuration_arguments(v,app,cpu,args.purpose)
    if args.synthesize:command.append('--synthesize')
    if args.minimal:
        if args.l2_size is not None:command+=['--l2-size',str(args.l2_size)]
        if args.boot_mode is not None:command+=['--boot-mode',args.boot_mode]
    return [sys.executable,str(ROOT/'scripts/build.py'),*command]


def perform(args):
    operation=Operation('build')
    print('Operation logs:',operation.path,flush=True)
    try:
        command=invocation(args)
        if args.minimal:
            configuration=('minimal','rv32im',0 if args.boot_mode=='xip' else 8192,args.l2_size or 4096)
        else:
            v=Build(args.template).validation
            mode=args.boot_mode or v.get('boot_mode','rom')
            configuration=(v['profile'],v['isa'],0 if mode=='xip' else (v['rom_size_bytes'] or (8192 if args.boot_mode=='rom' else 4096)),args.l2_size or v['l2_size_bytes'])
        output,_=run_path(*configuration,args.purpose)
        command+=['--output-dir',str(output)]
        operation.report['command']=command
        operation.report['output_dir']=str(output)
        operation.report['source']='current working tree; template supplies configuration, not historical source'
        if args.dry_run:
            operation.report['dry_run']=True
            operation.report['passed']=True
            print(json.dumps(operation.report,indent=2,ensure_ascii=False))
        else:
            # Keep live output for progress and independently preserve the build
            # invocation. build.py owns unique output directories and tool logs.
            subprocess.run(command,check=True,cwd=ROOT)
            info=json.loads((output/'build-info.json').read_text(encoding='utf-8'))
            operation.report['build_id']=info['id']
            operation.report['build_status']=info['status']
            print('Build ID:',info['id'],flush=True)
            print('Build result:',operation.path/'result.json',flush=True)
        operation.report['passed']=True
    except Exception as error:
        operation.report['error']=str(error);raise
    finally:operation.save()
