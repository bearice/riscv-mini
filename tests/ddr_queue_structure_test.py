"""Reject combinational feedback in the actual DDR controller/crossbar.

The DFI behavioral model can settle a zero-depth queue's feedback loop and
pass memory tests. Check signal dependencies as well; --depth 0 reproduces
the cross-bank lock/valid loop without synthesizing a complete SoC.
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from migen import Module
from migen.fhdl.structure import _Assign, If, Case
from migen.fhdl.tools import list_inputs, list_targets
from litedram.common import PhySettings
from litedram.core.controller import LiteDRAMController, ControllerSettings
from litedram.core.crossbar import LiteDRAMCrossbar
from gateware.ddr import H5TQ1G63EFR
from gateware.ddr_boot import DDR_CMD_BUFFER_DEPTH


def combinational_cycle(fragment):
    edges = {}

    def collect(statements, control):
        for statement in statements:
            if isinstance(statement, _Assign):
                inputs = list_inputs(statement.r) | control
                for target in list_targets(statement):
                    edges.setdefault(target, set()).update(inputs)
            elif isinstance(statement, If):
                inputs = list_inputs(statement.cond) | control
                collect(statement.t, inputs)
                collect(statement.f, inputs)
            elif isinstance(statement, Case):
                inputs = list_inputs(statement.test) | control
                for branch in statement.cases.values():
                    collect(branch, inputs)

    collect(fragment.comb, set())
    seen, active = set(), []

    def walk(node):
        if node in active:
            return active[active.index(node):] + [node]
        if node in seen:
            return None
        seen.add(node)
        active.append(node)
        for dependency in sorted(edges.get(node, ()), key=lambda s: s.duid):
            result = walk(dependency)
            if result:
                return result
        active.pop()
        return None

    for node in edges:
        result = walk(node)
        if result:
            return result
    return None


def check(depth):
    module = H5TQ1G63EFR(60e6, '1:2')
    phy = PhySettings(phytype='Simulation', memtype='DDR3', databits=16,
        dfi_databits=64, nphases=2, rdphase=0, wrphase=0, cl=6, cwl=6,
        read_latency=12, write_latency=2)
    dut = Module()
    dut.submodules.controller = controller = LiteDRAMController(phy,
        module.geom_settings, module.timing_settings, 60e6,
        ControllerSettings(cmd_buffer_depth=depth, with_auto_precharge=False))
    dut.submodules.crossbar = crossbar = LiteDRAMCrossbar(controller.interface)
    crossbar.get_port()  # The real SoC has one shared L2 native backend.
    cycle = combinational_cycle(dut.get_fragment())
    if cycle:
        names = ' -> '.join(signal.backtrace[-1][0] for signal in cycle)
        raise AssertionError(f'DDR depth={depth} combinational feedback: {names}')
    print(f'DDR depth={depth} PASS: no combinational feedback')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--depth', type=int, default=DDR_CMD_BUFFER_DEPTH)
    check(parser.parse_args().depth)
