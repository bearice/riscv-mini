"""Regression: vector CDC and external core SDC targets must cross module instances."""
import re
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gateware.rtl import qualify_sdc, normalize_clock_domains
source='''module riscv_mini__rgb_lcd();
reg [31:0] multiregimpl60;
always @(posedge sys_clk) multiregimpl60 <= incoming;
endmodule
module riscv_mini__usb_host();
wire usb_control_reset;
assign usb_control_reset = reset | disabled;
UsbOhciWishbone_Dw32_Pc1_Pf48000000 UsbOhciWishbone_Dw32_Pc1_Pf48000000();
endmodule
module riscv_mini();
endmodule
'''
modules=list(re.finditer(r'^module\s+(\w+)\b.*?^endmodule\b[^\n]*',source,re.M|re.S))
sdc='\n'.join(f'set_false_path -to [get_pins {{multiregimpl60_{bit}_s1/D}}]' for bit in range(32))
sdc+='\nset_max_delay 25 -from [get_pins {UsbOhciWishbone_Dw32_Pc1_Pf48000000/back_buffer_0_*write*_s*/Q}]'
sdc+='\nset_false_path -through [get_nets {usb_control_reset}]'
qualified=qualify_sdc(modules,'riscv_mini',sdc)
for bit in range(32):assert f'[get_pins {{rgb_lcd/multiregimpl60_{bit}_s1/D}}]' in qualified
assert '[get_pins {usb_host/UsbOhciWishbone_Dw32_Pc1_Pf48000000/back_buffer_0_*write*_s*/Q}]' in qualified
assert '[get_nets {usb_host/usb_control_reset}]' in qualified
assert qualify_sdc(modules,'riscv_mini',qualified)==qualified
print('Hierarchical vector CDC and external-core SDC qualification PASS')

# The hierarchical converter reads child _fragment.sync, whereas Migen's
# ClockDomainsRenamer only renames the merged parent's sync dictionary.
from migen import Module, Signal, ClockDomain, ClockDomainsRenamer, CEInserter, ResetInserter
from litex.gen import LiteXContext
from litex.gen.fhdl.verilog import convert

class Timer(Module):
    def __init__(self):
        self.count = Signal(8)
        self.sync += self.count.eq(self.count + 1)

class Receiver(Module):
    def __init__(self):
        self.submodules.timer = Timer()

class Top(Module):
    def __init__(self, controlled=False):
        self.clock_domains.cd_sys = ClockDomain('sys')
        self.clock_domains.cd_eth_rx = ClockDomain('eth_rx')
        receiver = Receiver()
        if controlled:
            receiver = CEInserter()(ResetInserter()(receiver))
        self.submodules.rx = ClockDomainsRenamer('eth_rx')(receiver)

for controlled in (False, True):
    top = Top(controlled)
    fragment = top.get_fragment()
    assert set(top.rx.timer._fragment.sync) == {'sys'}  # reproduce the defect
    effective = {k: tuple(map(id, v)) for k, v in fragment.sync.items()}
    normalize_clock_domains(top, fragment)
    assert set(top.rx.timer._fragment.sync) == {'eth_rx'}
    assert {k: tuple(map(id, v)) for k, v in fragment.sync.items()} == effective
    LiteXContext.top = top
    rtl = convert(fragment, ios={top.rx.timer.count}, name='domain_test', hierarchical=True).main_source
    assert 'always @(posedge eth_rx_clk)' in rtl
    assert 'always @(posedge sys_clk)' not in rtl
    if controlled:
        assert 'if (ce)' in rtl and 'if (reset)' in rtl
    else:
        assert 'module domain_test__rx__timer' in rtl
print('Nested clock renaming, reset and CE preservation PASS')
