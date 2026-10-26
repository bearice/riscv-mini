"""Use the installed Gowin PLL model to check real output periods/duty trim."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from migen import ClockDomain, Module, Signal
from migen.fhdl import verilog
from migen.genlib.resetsync import AsyncResetSynchronizer
from litex.build.gowin.common import GowinAsyncResetSynchronizer
from gateware.machines.mega138k_ae350.clock import FabricPLL

TB = r'''
`timescale 1ns/1ps
// GW5A's clock simulation library omits the ordinary fabric flop models.
module DFFP(output reg Q, input D,CLK,PRESET);
  always @(posedge CLK or posedge PRESET) if(PRESET)Q<=1'b1;else Q<=D;
endmodule
module tb;
  reg clk50=0,reset=1;
  wire sys_clk,cpu_clk,mem_clk,locked;
  always #10 clk50=~clk50;
  GSR GSR(.GSRI(1'b1));
  dut dut(.*);
  realtime start,high_time,period;
  integer i;
  initial begin
    #100;reset=0;
    wait(locked);repeat(5)@(posedge mem_clk);
    for(i=0;i<10;i=i+1)begin
      @(posedge mem_clk);start=$realtime;
      @(negedge mem_clk);high_time=$realtime-start;
      @(posedge mem_clk);period=$realtime-start;
      if(high_time<5.035 || high_time>5.065 || period<9.985 || period>10.015)
        $fatal(1,"100MHz data waveform: period=%f high=%f",period,high_time);
    end
    @(posedge sys_clk);start=$realtime;@(posedge sys_clk);period=$realtime-start;
    if(period<19.985 || period>20.015)$fatal(1,"50MHz fabric period %f",period);
    @(posedge cpu_clk);start=$realtime;@(posedge cpu_clk);period=$realtime-start;
    if(period<1.235 || period>1.265)$fatal(1,"800MHz core period %f",period);
    $display("PASS Gowin PLL model: 50/800/100MHz, data high/low 5.05/4.95ns");$finish;
  end
  initial begin #5000000;$fatal(1,"PLL model lock/clock timeout");end
endmodule
'''


class Platform:
    def add_generated_clock_constraint(self, *args, **kwargs):
        pass


class FabricPLLTest(unittest.TestCase):
    def test_physical_clock_waveforms(self):
        for fabric_hz in (50e6,):
            with self.subTest(fabric_hz=fabric_hz):
                self.check_physical_clock_waveforms(fabric_hz)

    def check_physical_clock_waveforms(self, fabric_hz):
        iv, vvp = shutil.which('iverilog'), shutil.which('vvp')
        self.assertTrue(iv and vvp, 'iverilog/vvp required')
        library = Path(os.environ['GOWIN_HOME']) / 'IDE/simlib/gw5a/prim_sim.v'
        self.assertTrue(library.is_file(), 'installed Gowin primitive simulation library required')
        m = Module()
        for name in ('sys', 'cpu', 'mem'):
            setattr(m.clock_domains, 'cd_' + name, ClockDomain(name))
        clk50 = Signal()
        m.submodules.pll = pll = FabricPLL(Platform(), clk50,
            [(m.cd_sys, fabric_hz), (m.cd_cpu, 800e6), (m.cd_mem, 100e6)], 50e6)
        ports = dict(clk50=clk50, reset=pll.reset, locked=pll.locked,
                     sys_clk=m.cd_sys.clk, cpu_clk=m.cd_cpu.clk, mem_clk=m.cd_mem.clk)
        for name, signal in ports.items():
            signal.name_override = name
        with tempfile.TemporaryDirectory(prefix='ae350-pll-') as directory:
            directory = Path(directory)
            (directory / 'dut.v').write_text(str(verilog.convert(m, ios=set(ports.values()), name='dut',
                special_overrides={AsyncResetSynchronizer: GowinAsyncResetSynchronizer})))
            testbench = TB
            (directory / 'tb.v').write_text(testbench)
            compiled = directory / 'test.vvp'
            subprocess.run([iv, '-g2012', '-s', 'tb', '-o', str(compiled),
                            str(directory / 'dut.v'), str(directory / 'tb.v'), str(library)], check=True)
            r = subprocess.run([vvp, str(compiled)], text=True, capture_output=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('PASS Gowin PLL', r.stdout)


if __name__ == '__main__':
    unittest.main()
