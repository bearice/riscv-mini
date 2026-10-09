"""Legacy MAC CSR writes cannot acquire packet ownership in a DMA build."""
from pathlib import Path
from types import SimpleNamespace
import sys
import argparse
import os
import subprocess
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from migen import Module, Signal
from migen.fhdl import verilog
from liteeth.mac.sram import LiteEthMACSRAM
from gateware.ethernet_ring import attach_ring_to_mac


def main():
    dut = Module()
    sram = LiteEthMACSRAM(32, 512, 2, 2, endianness='little')
    dut.submodules.ring = ring = Module()
    for name, width in [('active', 1), ('rx_release', 1), ('rx_valid', 1),
            ('rx_slot', 1), ('rx_length', 12), ('tx_start', 1), ('tx_slot', 1),
            ('tx_length', 12), ('tx_ready', 1), ('tx_done', 1)]:
        setattr(ring, name, Signal(width, name_override="ring_"+name))
    pio_start, pio_slot, pio_length = (sram.reader._start.wr_stb,
        sram.reader._slot.storage, sram.reader._length.storage)
    commands = [sram.reader._start, sram.reader._slot, sram.reader._length]
    mac = SimpleNamespace(interface=SimpleNamespace(sram=sram), csrs=sram.get_csrs())
    attach_ring_to_mac(ring, mac)
    assert not any(csr is command for csr in mac.csrs for command in commands)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iverilog', required=True)
    compiler = Path(parser.parse_args().iverilog).resolve()
    from migen.fhdl.structure import _Assign
    # Observe the actual FIFO handshake assignments without the packet datapath.
    rx_ready = sram.writer.stat_fifo.source.ready
    tx_assignments = [stmt for stmt in sram.reader._fragment.comb
        if isinstance(stmt, _Assign) and any(stmt.r is signal for signal in
            (ring.tx_start, ring.tx_slot, ring.tx_length))]
    dut.comb += [stmt for stmt in sram.writer._fragment.comb
        if isinstance(stmt, _Assign) and stmt.l is rx_ready]
    dut.comb += tx_assignments
    command_valid, command_slot, command_length = [stmt.l for stmt in tx_assignments]
    ports = dict(active=ring.active, rx_release=ring.rx_release, tx_start=ring.tx_start,
        tx_slot=ring.tx_slot, tx_length=ring.tx_length,
        pio_start=pio_start, pio_slot=pio_slot,
        pio_length=pio_length, pio_release=sram.writer.ev.available.clear,
        command_valid=command_valid, command_slot=command_slot, command_length=command_length, released=sram.writer.stat_fifo.source.ready)
    for name, signal in ports.items():
        signal.name_override = name
    tb = r'''
module tb;
reg sys_clk=0,sys_rst=1;always #5 sys_clk=~sys_clk;
reg active=0,rx_release=0,tx_start=0,tx_slot=0,pio_start=0,pio_slot=1,pio_release=0;
reg [11:0] pio_length=64;reg [11:0] tx_length=32;
wire command_valid, command_slot, released;wire [11:0] command_length;
mac dut(.*);
initial begin
 repeat(3)@(negedge sys_clk);sys_rst=0;
 pio_start=1;pio_release=1;
 repeat(5)begin @(negedge sys_clk);
  if(command_valid || released)$fatal(1,"legacy CSR acquired MAC packet ownership");
 end
 pio_start=0;pio_release=0;rx_release=1;tx_start=1;
 @(negedge sys_clk);
 if(!command_valid || command_slot || command_length!=32 || !released)$fatal(1,"ring handshake lost");
 tx_start=0;rx_release=0;
 $display("Ethernet exclusive ownership PASS: stopped ring rejects legacy CSR TX/RX, ring handshakes work");
 $finish;
end
initial begin #10000;$fatal(1,"timeout");end
endmodule
'''
    with tempfile.TemporaryDirectory(prefix='eth-ownership-', dir=Path.cwd()/'build') as tmp:
        out = Path(tmp)
        env = dict(os.environ, PATH=str(compiler.parent)+os.pathsep+os.environ.get('PATH',''), TEMP=tmp, TMP=tmp, TMPDIR=tmp)
        source = str(verilog.convert(dut, ios=set(ports.values()), name='mac'))
        (out/'mac.v').write_text(source)
        (out/'tb.v').write_text(tb)
        subprocess.run([str(compiler), '-g2012', '-s', 'tb', '-o', str(out/'sim'),
            str(out/'mac.v'), str(out/'tb.v')], env=env, check=True)
        subprocess.run([str(compiler.with_name('vvp.exe')), str(out/'sim')], env=env, check=True, timeout=30)


if __name__ == '__main__':
    main()
