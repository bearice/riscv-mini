"""Exercise shallow bank queues through the real controller and native crossbar.

The DFI memory model has reduced row count for simulation speed. This checks
protocol, masks, bank/row changes and refresh, not the physical GW2 DDR PHY.
"""
import sys
import argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from migen import Module, Memory, Signal
from migen.sim import run_simulation
from litedram.common import PhySettings
from litedram.core.controller import LiteDRAMController, ControllerSettings
from litedram.core.crossbar import LiteDRAMCrossbar
from litedram.phy.model import SDRAMPHYModel
from gateware.ddr import H5TQ1G63EFR
from gateware.ddr_boot import DDR_CMD_BUFFER_DEPTH


class SmallMemory(H5TQ1G63EFR):
    nrows = 8


def exercise(depth):
    module = SmallMemory(60e6, '1:2')
    # DDR commands still carry A10 (all-bank precharge), even with few rows.
    module.geom_settings.addressbits = 13
    settings = PhySettings(phytype='Simulation', memtype='DDR3', databits=16,
        dfi_databits=64, nphases=2, rdphase=0, wrphase=0, cl=6, cwl=6,
        read_latency=12, write_latency=2)
    dut = Module()
    dut.submodules.phy = phy = SDRAMPHYModel(module, settings=settings, clk_freq=60e6)
    dut.submodules.ctrl = ctrl = LiteDRAMController(settings, module.geom_settings,
        module.timing_settings, 60e6, ControllerSettings(cmd_buffer_depth=depth,
            with_auto_precharge=False))
    dut.submodules.crossbar = crossbar = LiteDRAMCrossbar(ctrl.interface)
    dut.comb += ctrl.dfi.connect(phy.dfi)
    port = crossbar.get_port()
    expected = {}

    def handshake(channel):
        for _ in range(2000):
            yield
            if (yield channel.ready):
                return
        raise AssertionError('Native handshake timeout')

    def command(address, write):
        yield port.cmd.addr.eq(address)
        yield port.cmd.we.eq(write)
        yield port.cmd.valid.eq(1)
        yield from handshake(port.cmd)
        yield port.cmd.valid.eq(0)

    def write(address, value, mask):
        # Raw native write data is also consumed on a fixed-latency pulse.
        # SharedL2 holds data before issuing the command and enters WRITE
        # immediately afterwards; do not model AXI-style delayed write data.
        yield port.wdata.data.eq(value)
        yield port.wdata.we.eq(mask)
        yield from command(address, 1)
        yield port.wdata.valid.eq(1)
        yield from handshake(port.wdata)
        yield port.wdata.valid.eq(0)
        old = expected.get(address, 0)
        for byte in range(16):
            if mask & (1 << byte):
                old = (old & ~(255 << (8*byte))) | (value & (255 << (8*byte)))
        expected[address] = old
        yield

    def read(address):
        yield from command(address, 0)
        # Raw LiteDRAM native read responses are pulses, not a backpressurable
        # stream. SharedL2 enters READ/REFILL_DATA immediately after command.
        yield port.rdata.ready.eq(1)
        for _ in range(2000):
            yield
            if (yield port.rdata.valid):
                break
        else:
            raise AssertionError('Read timeout')
        assert (yield port.rdata.data) == expected[address], (depth, address,
            hex((yield port.rdata.data)), hex(expected[address]))
        yield
        yield port.rdata.ready.eq(0)
        yield

    def drive():
        # Column, bank and row bits all vary; revisit open and closed rows.
        addresses = [row*1024 + bank*128 + col for row in range(3)
                     for bank in range(8) for col in (0, 3)]
        for address in addresses:
            value = sum(((address*7 + i*19) & 255) << (8*i) for i in range(16))
            yield from write(address, value, 0xffff)
        for address in reversed(addresses):
            yield from read(address)
            yield from write(address, (1 << 128)-1, 0xa55a)
            yield from read(address)
        # Idle long enough for multiple refreshes, then verify retention.
        for _ in range(1000):
            yield
        for address in addresses:
            yield from read(address)

    fragment = dut.get_fragment()
    # This Migen simulator lowers every RAM port as readable; LiteX FIFOs
    # legitimately use write-only ports. Supply an unused simulation sink.
    for memory in fragment.specials:
        if isinstance(memory, Memory):
            for ram_port in memory.ports:
                if ram_port.dat_r is None:
                    ram_port.dat_r = Signal(memory.width)
    run_simulation(fragment, drive())
    print(f'DDR bank queue depth={depth} PASS: 48 addresses, masks, row/bank changes, native data timing, refresh')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--depth', type=int, choices=(0, 1, 8), action='append')
    for depth in parser.parse_args().depth or (DDR_CMD_BUFFER_DEPTH,):
        exercise(depth)
