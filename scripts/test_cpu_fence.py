"""Execute fence/AMO sequences on native CPU RTL with ACK-visible memory."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpu',required=True,type=Path)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'build/cpu-fence-test')
    parser.add_argument('--iverilog',type=Path)
    parser.add_argument('--compressed',action='store_true',help='Exercise C instructions and cross-line fetch')
    parser.add_argument('--ack-delay',type=int,default=9,help='Cycles before a data-bus ACK (positive)')
    args=parser.parse_args();output=args.output_dir.resolve();output.mkdir(parents=True,exist_ok=True)
    if args.ack_delay<1:parser.error('--ack-delay must be positive')
    gcc=Path(json.loads((ROOT/'.tools.local.json').read_text())['gcc'])
    iv=args.iverilog or shutil.which('iverilog')
    if not iv:parser.error('Icarus Verilog required; pass --iverilog')
    iv=Path(iv).resolve();tmp=output/'tmp';tmp.mkdir(exist_ok=True)
    env={**os.environ,'PATH':str(gcc.parent)+os.pathsep+str(iv.parent)+os.pathsep+os.environ['PATH'],
         'TMP':str(tmp),'TEMP':str(tmp)}
    arch='rv32imac_zicsr_zifencei' if args.compressed else 'rv32ima_zicsr_zifencei'
    defines=['-DTEST_COMPRESSED'] if args.compressed else []
    subprocess.run([str(gcc),'-march='+arch,*defines,'-mabi=ilp32','-nostdlib','-nostartfiles',
        '-Wl,-Ttext=0','-Wl,--build-id=none',str(ROOT/'tests/cpu_fence_program.S'),'-o',str(output/'program.elf')],check=True,env=env)
    subprocess.run([str(gcc.parent/'riscv-none-elf-objcopy.exe'),'-O','binary',str(output/'program.elf'),str(output/'program.bin')],check=True,env=env)
    data=(output/'program.bin').read_bytes();data+=bytes(16384-len(data))
    (output/'program.hex').write_text('\n'.join(f'{int.from_bytes(data[i:i+4],"little"):08x}' for i in range(0,len(data),4))+'\n')
    subprocess.run([str(iv),'-g2012','-s','cpu_fence_tb',f'-Pcpu_fence_tb.ACK_DELAY={args.ack_delay}','-o',str(output/'test.vvp'),
        str(ROOT/'tests/cpu_fence_tb.v'),str(args.cpu.resolve())],check=True,env=env)
    vvp=iv.parent/('vvp.exe' if os.name=='nt' else 'vvp')
    result=subprocess.run([str(vvp),str(output/'test.vvp')],cwd=output,env=env,text=True,capture_output=True)
    (output/'simulation.log').write_text(result.stdout+result.stderr)
    print(result.stdout+result.stderr,end='');result.check_returncode()
    if 'CPU RTL fence PASS' not in result.stdout:raise RuntimeError('Missing completion marker')


if __name__=='__main__':main()
