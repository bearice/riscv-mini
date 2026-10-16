"""Run a prepared upstream build using an automatically selected Linux executor."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from scripts.modular.environment import linux_runner
from scripts.modular.storage import Run, write


def linux_path(path):
    path=Path(path).resolve().as_posix()
    if os.name!='nt':return path
    return '/mnt/'+path[0].lower()+path[2:]


def execute(script, arguments=(), required=('cc','make'), run=None):
    environment=linux_runner(required)
    owned=run is None
    run=run or Run('build','linux-upstream',dict(script=str(script),arguments=list(arguments)))
    write(run.path/'environment.json',environment)
    # Explicit host compiler for upstream configure/Kbuild tools.
    bindings={'HOSTCC':environment['tools']['cc']['path']} if 'cc' in environment['tools'] else {}
    if 'cxx' in environment['tools']:bindings['HOSTCXX']=environment['tools']['cxx']['path']
    command=[*environment['prefix'],'env',*[k+'='+v for k,v in bindings.items()], 'bash',linux_path(script),*arguments]
    run.result.update(command=command,environment=environment)
    try:
        with (run.path/'build.log').open('w',encoding='utf-8') as log:
            proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            for line in proc.stdout:
                print(line,end='',flush=True);log.write(line);log.flush()
            if proc.wait():raise RuntimeError('Upstream build failed: '+str(run.path/'build.log'))
        if owned:run.finish()
        else:run.save()
    except BaseException as error:
        run.result.update(passed=False,error=str(error));run.save();raise
    return run
