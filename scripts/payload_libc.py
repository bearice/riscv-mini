"""Record the exact Newlib multilib selected by a payload's ISA and ABI."""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'firmware/libc/runtime.c'

def newlib_link(gcc,march,mabi,out):
    libraries={}
    for name in ('libc.a','libm.a','libgcc.a'):
        selected=subprocess.check_output(
            [str(gcc),'-march='+march,'-mabi='+mabi,'-print-file-name='+name],
            text=True).strip()
        path=Path(selected)
        if not path.is_file():
            raise ValueError(f'{march}/{mabi}: toolchain did not provide {name}: {selected}')
        libraries[name]={'path':str(path.resolve()),
                         'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest={'libc':'newlib','linkage':'static','march':march,'mabi':mabi,
              'libraries':libraries,'runtime_sha256':hashlib.sha256(RUNTIME.read_bytes()).hexdigest(),
              'runtime_header_sha256':hashlib.sha256(RUNTIME.with_suffix('.h').read_bytes()).hexdigest()}
    (out/'libc-inputs.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return ['-Wl,--start-group','-lc','-lm','-lgcc','-Wl,--end-group']
