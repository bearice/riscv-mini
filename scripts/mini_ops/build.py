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
             '--cpu-variant',v['cpu_variant'],'--cpu-verilog',str(Path(cpu).resolve()),
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
        v=template.validation
        app=args.app or Path(v.get('application_source',''))
        if not app.is_file():raise ValueError('Template application source missing; select --app')
        cpu=Path(v.get('cpu_verilog',''))
        if not cpu.is_file():raise ValueError('Template CPU RTL missing; reproduce the template first')
        command=configuration_arguments(v,app,cpu,args.purpose)
    if args.synthesize:command.append('--synthesize')
    return [sys.executable,str(ROOT/'scripts/build.py'),*command]


def perform(args):
    operation=Operation('build')
    print('Operation logs:',operation.path,flush=True)
    try:
        command=invocation(args)
        if args.minimal:
            configuration=('minimal','rv32im',8192,4096)
        else:
            v=Build(args.template).validation
            configuration=(v['profile'],v['isa'],v['rom_size_bytes'],v['l2_size_bytes'])
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
