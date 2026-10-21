"""Replay the real UART-loaded memory diagnostic on native CPU RTL.

Models byte-lane writes and ACK-visible source/stack/scratch memory, plus UART.
No DDR PHY or shared L2: this separates CPU behavior from the board memory path.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cpu',required=True,type=Path)
    p.add_argument('--image',required=True,type=Path,help='boot_memory_test app.bin')
    p.add_argument('--csr-json',required=True,type=Path)
    p.add_argument('--iverilog',required=True,type=Path)
    p.add_argument('--output-dir',required=True,type=Path)
    p.add_argument('--ack-delay',type=int,default=9)
    a=p.parse_args()
    if a.ack_delay<1:p.error('ACK delay must be positive')
    out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    raw=a.image.read_bytes()
    if len(raw)>16384:p.error('Diagnostic must fit the 16 KiB source region')
    raw+=bytes(16384-len(raw))
    (out/'program.hex').write_text('\n'.join(f'{int.from_bytes(raw[i:i+4],"little"):08x}' for i in range(0,len(raw),4))+'\n')
    csr=json.loads(a.csr_json.read_text())['csr_registers']
    iv=a.iverilog.resolve()
    vvp=iv.parent/('vvp.exe' if os.name=='nt' else 'vvp')
    tmp=out/'tmp';tmp.mkdir(exist_ok=True)
    env={**os.environ,'PATH':str(iv.parent)+os.pathsep+os.environ['PATH'],'TMP':str(tmp),'TEMP':str(tmp)}
    subprocess.run([str(iv),'-g2012','-s','cpu_copy_crc_tb',
        f'-Pcpu_copy_crc_tb.ACK_DELAY={a.ack_delay}',
        f'-Pcpu_copy_crc_tb.UART_RXTX={csr["uart_rxtx"]["addr"]}',
        f'-Pcpu_copy_crc_tb.UART_RXEMPTY={csr["uart_rxempty"]["addr"]}',
        '-o',str(out/'test.vvp'),str(ROOT/'tests/primer20k_vexriscv/cpu_copy_crc_tb.v'),str(a.cpu.resolve())],check=True,env=env)
    result=subprocess.run([str(vvp),str(out/'test.vvp')],cwd=out,env=env,text=True,capture_output=True)
    (out/'simulation.log').write_text(result.stdout+result.stderr)
    print(result.stdout+result.stderr,end='');result.check_returncode()
    if 'CPU copy/CRC PASS:' not in result.stdout:raise RuntimeError('Missing success marker')
if __name__=='__main__':main()
