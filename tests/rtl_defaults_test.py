"""Wishbone ERR, a shared default=1 Signal, and parent/child reset priority.

Generate fixtures for Icarus; --unfixed demonstrates the original conversion
failure. This tests the real hierarchical conversion adapter, not RTL strings.
"""
import argparse
from pathlib import Path
from types import SimpleNamespace
import sys
import subprocess
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import Signal, ClockDomain, ResetInserter
from litex.gen import LiteXModule, LiteXContext
from litex.build.generic_platform import GenericPlatform
from litex.soc.interconnect import wishbone
from gateware.rtl import split_verilog


class Consumer(LiteXModule):
    def __init__(self, err, default_high):
        self.err=Signal()
        self.high=Signal()
        self.comb += [self.err.eq(err),self.high.eq(default_high)]


class Fixture(LiteXModule):
    def __init__(self):
        self.cd_sys=ClockDomain('sys')
        self.rom=wishbone.SRAM(64,read_only=True,init=[0x12345678])
        self.group=ResetInserter(['sys'])(CounterParent())
        self.local_reset=Signal(name_override='local_reset')
        self.count=Signal(8,name_override='count')
        default_high=Signal(reset=1)
        self.consumer=Consumer(self.rom.bus.err,default_high)
        self.protocol_err=Signal(name_override='protocol_err')
        self.default_high=Signal(name_override='default_high')
        self.comb += [self.protocol_err.eq(self.consumer.err),
                     self.default_high.eq(self.consumer.high),
                     self.rom.bus.dat_w.eq(default_high),
                     self.group.reset_sys.eq(self.local_reset),
                     self.count.eq(self.group.child.count)]


class Counter(LiteXModule):
    def __init__(self):
        self.count=Signal(8)
        self.sync += self.count.eq(self.count+1)


class CounterParent(LiteXModule):
    def __init__(self):
        self.child=Counter()
        self.alias=self.child


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--unfixed',action='store_true')
    p.add_argument('--flat',action='store_true')
    p.add_argument('--blocks',action='store_true',help='Exercise default SoC block hierarchy instead of deep hierarchy')
    p.add_argument('--unfixed-order',action='store_true',help='Keep defaults fix but reproduce old reset priority')
    p.add_argument('--unfixed-alias',action='store_true',help='Reproduce dropped body of a shared module')
    p.add_argument('--run',action='store_true')
    p.add_argument('--iverilog',default='iverilog')
    p.add_argument('--vvp',default='vvp')
    p.add_argument('--wsl-distro',help='Use installed Linux tools from a Windows checkout')
    args=p.parse_args();out=args.output_dir;out.mkdir(parents=True,exist_ok=True)
    dut=Fixture();LiteXContext.top=dut
    platform=GenericPlatform('virtual',[])
    platform.toolchain=SimpleNamespace(build_timing_constraints=lambda ns:None,options={})
    if args.blocks and (args.flat or args.unfixed):p.error('--blocks requires the fixed hierarchical converter')
    if not args.unfixed and not args.flat:split_verilog(platform,deep=not args.blocks)
    if args.unfixed_order:
        import gateware.rtl as adapter
        original=adapter.normalize_clock_domains
        def no_order(*args):original(*args);return {}
        adapter.normalize_clock_domains=no_order
    if args.unfixed_alias:
        import gateware.rtl as adapter
        adapter.remove_shared_aliases=lambda node:node
    fragment=dut.get_fragment()
    result=platform.get_verilog(fragment,ios={dut.protocol_err,dut.default_high,dut.local_reset,dut.count,dut.cd_sys.clk,dut.cd_sys.rst},
                               name='fixture',hierarchical=not args.flat)
    (out/'fixture.v').write_text(result.main_source)
    for name,content in result.data_files.items():(out/name).write_text(content)
    (out/'tb.v').write_text('''`timescale 1ns/1ps
module tb;
reg clk=0,rst=1,local_reset=0;always #5 clk=~clk;
wire err,high;
wire [7:0] count;
fixture dut(.sys_clk(clk),.sys_rst(rst),.protocol_err(err),.default_high(high),.local_reset(local_reset),.count(count));
initial begin
#21;
if(err !== 1'b0 || high !== 1'b1) $fatal(1,"DEFAULTS_FAIL err=%b high=%b",err,high);
$display("DEFAULTS_PASS err=%b high=%b",err,high);
rst=0;#30;if(count<2) $fatal(1,"COUNTER_NOT_RUNNING count=%d",count);
local_reset=1;#30;if(count !== 0) $fatal(1,"RESET_PRIORITY_FAIL count=%d",count);
local_reset=0;#20;if(count !== 2) $fatal(1,"RESET_RELEASE_FAIL count=%d",count);
$display("RESET_PRIORITY_PASS count=%d",count);$finish;
end
endmodule
''')
    print('RTL defaults fixture:',out)
    if args.run:
        prefix=[]
        if args.wsl_distro:
            absolute=out.resolve().as_posix()
            linux='/mnt/'+absolute[0].lower()+absolute[2:]
            prefix=['wsl','-d',args.wsl_distro,'--cd',linux,'--exec']
        for name,command in [('compile',[args.iverilog,'-g2012','-s','tb','-o','test.vvp','fixture.v','tb.v']),
                             ('result',[args.vvp,'test.vvp'])]:
            result=subprocess.run(prefix+command,cwd=out,capture_output=True,text=True,timeout=30)
            (out/(name+'.log')).write_text(result.stdout+result.stderr)
            if result.returncode:raise SystemExit(result.stdout+result.stderr)
        if not all(marker in result.stdout for marker in ('DEFAULTS_PASS','RESET_PRIORITY_PASS')):
            raise SystemExit('Missing simulation pass marker')
        print(result.stdout)


if __name__=='__main__':main()
