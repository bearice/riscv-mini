"""Read-only SPI NOR Wishbone window, with a software SPI pad owner."""
from migen import Signal, Constant, Cat, Mux, FSM, If, NextValue, NextState
from litex.gen import LiteXModule
from litex.soc.interconnect import wishbone
from litex.soc.interconnect.csr import CSRStatus, CSRStorage
from gateware.memory_map import XIP_BASE, XIP_WINDOW_SIZE

FLASH_BASE = XIP_BASE
FLASH_SIZE = XIP_WINDOW_SIZE
XIP_OFFSET = 0x100000
XIP_SIZE = 0x100000  # [1,2) MiB; the DDR app image starts at 2 MiB.


class FlashXIP(LiteXModule):
    def __init__(self, pads, software, half_period=3, wake_cycles=180):
        self.bus = bus = wishbone.Interface(data_width=32, address_width=32, addressing='word')
        self._enable = CSRStorage(reset=1,name='enable',description='Permit new XIP reads; active transaction finishes before disabling.')
        owned = Signal()
        self._busy = CSRStatus(name='busy',description='XIP owns the Flash pins; software SPI must wait.')
        clk = Signal()
        cs_n = Signal(reset=1)
        tx = Signal(64)
        rx = Signal(32)
        bit = Signal(6)
        divider = Signal(max=half_period)
        delay = Signal(max=wake_cycles)
        request = Signal()
        self.comb += [
            self._busy.status.eq(owned),
            request.eq(bus.cyc & bus.stb),
            pads.clk.eq(Mux(owned, clk, software.clk)),
            pads.cs_n.eq(Mux(owned, cs_n, software.cs_n)),
            pads.mosi.eq(Mux(owned, tx[63], software.mosi)),
            software.miso.eq(pads.miso),
            bus.dat_r.eq(Cat(rx[24:32], rx[16:24], rx[8:16], rx[0:8])),
        ]
        self.fsm = fsm = FSM(reset_state='WAKE')
        fsm.act('WAKE',
            owned.eq(1),
            NextValue(tx, 0xab << 56), NextValue(bit, 7),
            NextValue(cs_n, 0), NextValue(divider, 0), NextState('WAKE_START'))
        # The NOR may still be in deep power-down following FPGA configuration.
        fsm.act('WAKE_START', owned.eq(1), NextState('SHIFT_WAKE'))
        for state, finish in [('SHIFT_WAKE', 'WAKE_WAIT'), ('SHIFT_READ', 'REPLY')]:
            fsm.act(state, owned.eq(1),
                If(divider == half_period-1,
                    NextValue(divider, 0),
                    If(~clk,
                        NextValue(clk, 1), NextValue(rx, Cat(pads.miso, rx[:31]))
                    ).Else(
                        NextValue(clk, 0), NextValue(tx, tx << 1),
                        If(bit == 0, NextValue(cs_n, 1), NextValue(delay, 0), NextState(finish))
                        .Else(NextValue(bit, bit-1))
                    )
                ).Else(NextValue(divider, divider+1)))
        fsm.act('WAKE_WAIT', owned.eq(1),
            If(delay == wake_cycles-1, NextState('IDLE')).Else(NextValue(delay, delay+1)))
        fsm.act('IDLE',
            If(request,
                If(bus.we | ~self._enable.storage, bus.err.eq(1)).Elif(software.cs_n,
                    # Word address is relative to the 4 MiB Flash window.
                    NextValue(tx, Cat(Constant(0, 32), Constant(0, 2), bus.adr[:22], Constant(3, 8))),
                    NextValue(bit, 63), NextValue(rx, 0), NextValue(cs_n, 0),
                    NextValue(divider, 0), NextState('SHIFT_READ'))))
        fsm.act('REPLY', owned.eq(1), bus.ack.eq(request), NextState('IDLE'))
