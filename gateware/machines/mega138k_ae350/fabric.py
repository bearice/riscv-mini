"""P3a fabric: SYSINFO, IRQ, DMA-service CSR, and one loopback data word.

The hard DMAC register block stays inside the macro at DMAC_BASE. This module
does not program it and does not claim that the DMAC can reach DDR or the
loopback. MUG1031 enables eight external DMA_REQ/ACK pairs and sixteen EXT_INT
inputs; it does not give the PLIC source number, so EXT_INT[0] is only the
fabric wire.

Loopback DATA is the aligned word at LOOPBACK_DATA. A write pushes and a read
pops. Any other size, address, or a push/pop that the FIFO cannot accept
returns a two-cycle ERROR and does not change the queue. The beat itself is
the AHB data phase. HWDATA is sampled there, not in the address phase: a real
master still drives the previous beat's data while the new address is accepted.

DMA_REQ[0] asks for one push and DMA_REQ[1] asks for one pop. Each request
stays high until its DMA_ACK is high, then stays low until that ACK has fallen,
and rises again only when another beat fits. ACK is counted on its rising edge
and does not move data. The hard macro launches that edge on its own clock, so
a fabric-edge sample can miss it. ack_catch holds the edge until the next
fabric clock. The synchronous sample stays in the count so simulation, which
does not execute the catcher, still sees an ack that is high at the clock.
"""
from functools import reduce
from operator import or_

from migen import *
from migen.fhdl.specials import Special, SPECIAL_INPUT, SPECIAL_OUTPUT
from litex.gen import LiteXModule
from litex.soc.interconnect.ahb import AHBInterface

from gateware.machines.mega138k_ae350.interconnect import ErrorSlave, MemInterconnect
from gateware.machines.mega138k_ae350 import address_map as amap
from gateware.machines.mega138k_ae350.peripherals.board_io import FabricBoard
from gateware.machines.mega138k_ae350.peripherals.board_sense import BoardSense
from gateware.machines.mega138k_ae350.peripherals.audio import FabricAudio


def _onehot(off, offsets):
    return reduce(or_, (off == item for item in offsets))


class _AckCatchSim(Module):
    """Simulation stand-in: a full clock of ack is already visible to the sampler."""

    def __init__(self, ack, caught):
        self.sync += caught.eq(ack)


class AckCatch(Special):
    """Hold an asynchronous ack edge until the next fabric clock."""

    def __init__(self, ack, caught):
        Special.__init__(self)
        self.ack = wrap(ack)
        self.caught = wrap(caught)

    def iter_expressions(self):
        yield self, "ack", SPECIAL_INPUT
        yield self, "caught", SPECIAL_OUTPUT

    @staticmethod
    def lower(dr):
        return _AckCatchSim(dr.ack, dr.caught)


class _GowinAckCatch(Module):
    def __init__(self, ack, caught):
        # D is 0, so the flop falls on the fabric clock once PRESET is released.
        # PRESET is level-sensitive and catches a pulse that misses that clock.
        self.specials += Instance("DFFP",
            i_CLK=ClockSignal(), i_D=0, i_PRESET=ack, o_Q=caught)


class GowinAckCatch:
    @staticmethod
    def lower(dr):
        return _GowinAckCatch(dr.ack, dr.caught)


class LoopbackFifo(LiteXModule):
    def __init__(self, bus, depth=amap.LOOPBACK_DEPTH):
        self.bus = bus
        self.clear = Signal()
        self.clear_overflow = Signal()
        self.clear_underflow = Signal()
        self.req_enable = Signal()
        self.level = Signal(max=depth + 1)
        self.empty = Signal()
        self.full = Signal()
        self.overflow = Signal()
        self.underflow = Signal()
        self.irq_set = Signal()
        self.req_tx = Signal()
        self.req_rx = Signal()
        self.ack_tx = Signal()
        self.ack_rx = Signal()
        slots = Array(Signal(32) for _ in range(depth))
        head = Signal(max=depth)
        tail = Signal(max=depth)
        count = Signal(max=depth + 1)
        tx_hold = Signal()
        rx_hold = Signal()
        rdata_q = Signal(32)
        write_q = Signal()
        bad = Signal()
        self.comb += [
            self.level.eq(count),
            self.empty.eq(count == 0),
            self.full.eq(count == depth),
            self.req_tx.eq(self.req_enable & (count != depth) & ~self.clear & ~tx_hold & ~self.ack_tx),
            self.req_rx.eq(self.req_enable & (count != 0) & ~self.clear & ~rx_hold & ~self.ack_rx),
        ]
        fsm = FSM(reset_state='IDLE')
        self.submodules.fsm = fsm
        capture = [
            NextValue(write_q, bus.write),
            NextValue(bad, (bus.size != 2) | (bus.addr[:2] != 0) | (bus.addr != amap.LOOPBACK_DATA)),
            NextState('WORK')]
        fsm.act('IDLE', bus.readyout.eq(1), If(bus.trans[1], *capture))
        rejected = Signal()
        # Keep this net. The APB error decode is a separate cone; if synthesis
        # folds the two together, EXTS_HRESP picks up the APB comparator.
        rejected.attr.add(("syn_keep", 1))
        self.comb += rejected.eq(bad | (write_q & self.full) | (~write_q & self.empty) | self.clear)
        # resp stays at the state register. Driving it from `rejected` in this
        # wait cycle pulls the comparator onto EXTS_HRESP, and that route
        # misses the 80 MHz setup by a fraction of a nanosecond.
        fsm.act('WORK', bus.readyout.eq(0),
            If(rejected, NextState('ERR')).Else(NextState('DONE')))
        fsm.act('ERR', bus.readyout.eq(1), bus.resp.eq(1),
            If(bus.trans[1], *capture).Else(NextState('IDLE')))
        fsm.act('DONE', bus.readyout.eq(1), bus.rdata.eq(rdata_q),
            If(bus.trans[1], *capture).Else(NextState('IDLE')))
        push = fsm.ongoing('WORK') & write_q & ~rejected
        pop = fsm.ongoing('WORK') & ~write_q & ~rejected
        overflow_ev = fsm.ongoing('WORK') & write_q & ~bad & self.full & ~self.clear
        underflow_ev = fsm.ongoing('WORK') & ~write_q & ~bad & self.empty & ~self.clear
        self.sync += [
            self.irq_set.eq(0),
            If(self.clear,
                head.eq(0), tail.eq(0), count.eq(0),
                tx_hold.eq(0), rx_hold.eq(0),
                self.overflow.eq(0), self.underflow.eq(0),
            ).Else(
                tx_hold.eq(self.ack_tx),
                rx_hold.eq(self.ack_rx),
                If(push,
                    slots[tail].eq(bus.wdata),
                    tail.eq(Mux(tail == depth - 1, 0, tail + 1)),
                    count.eq(count + 1),
                    self.irq_set.eq(1),
                ).Elif(pop,
                    rdata_q.eq(slots[head]),
                    head.eq(Mux(head == depth - 1, 0, head + 1)),
                    count.eq(count - 1),
                ),
                If(overflow_ev, self.overflow.eq(1)).Elif(self.clear_overflow, self.overflow.eq(0)),
                If(underflow_ev, self.underflow.eq(1)).Elif(self.clear_underflow, self.underflow.eq(0)),
            ),
        ]


class AE350Fabric(LiteXModule):
    """Extended AHB decode plus the platform APB pages owned by P3a."""

    def __init__(self, cpu, diagnostics, io, layout, video=None, sd=None):
        amap = layout
        self.video = video
        fifo_bus = AHBInterface(data_width=32, address_width=32)
        err_bus = AHBInterface(data_width=32, address_width=32)
        self.fifo = LoopbackFifo(fifo_bus)
        self.audio = FabricAudio(clock_hz=io.machine.clocks.sys)
        self.err = ErrorSlave(err_bus)
        if sd is not None:
            for bit,ack in ((amap.SD_TX_REQ,sd.tx_ack),(amap.SD_RX_REQ,sd.rx_ack)):
                caught=Signal()
                self.specials += AckCatch(cpu.dma_ack[bit],caught)
                self.comb += ack.eq(caught | cpu.dma_ack[bit])
        # HSEL is the macro's decode of the 128MiB extended-AHB window.
        sd_slaves = [] if sd is None else [(amap.SD_TX_DATA,4,sd.tx_bus),(amap.SD_RX_DATA,4,sd.rx_bus)]
        self.ic = MemInterconnect(cpu.ahb_exts, [
            *sd_slaves,
            (amap.AUDIO_DATA, 0x10000, self.audio.tx_bus),
            (amap.MIC_DATA, 0x10000, self.audio.rx_bus),
            (amap.LOOPBACK_DATA, 4, fifo_bus),
            (0, 1 << 32, err_bus),
        ], require_sel=True, registered=True)
        self.irq_enable = Signal()
        self.irq_pending = Signal()
        self.req_enable = Signal()
        ack_q = Signal(8)
        self.ack_tx = Signal(16)
        self.ack_rx = Signal(16)
        catch_tx = Signal()
        catch_rx = Signal()
        level_tx = Signal()
        level_rx = Signal()
        self.specials += [
            AckCatch(cpu.dma_ack[0], catch_tx),
            AckCatch(cpu.dma_ack[1], catch_rx),
        ]
        for bit, signal in ((amap.AUDIO_REQ, self.audio.tx_ack), (amap.MIC_REQ, self.audio.rx_ack)):
            caught = Signal()
            self.specials += AckCatch(cpu.dma_ack[bit], caught)
            self.comb += signal.eq(caught | cpu.dma_ack[bit])
        self.board = FabricBoard(io, clock_hz=io.machine.clocks.sys)
        self.sense = BoardSense(clock_hz=io.machine.clocks.sys)
        wait = Signal()
        access_q = Signal()
        addr_q = Signal(32)
        wdata_q = Signal(32)
        write_q = Signal()
        strb_q = Signal(4)
        read_data = Signal(32)
        error = Signal()
        error_q = Signal()
        self.sync += error_q.eq(error)
        error.attr.add(("syn_keep", 1))
        off = Signal(12)
        commit = Signal()
        self.comb += [
            cpu.apb_ready.eq(~wait),
            off.eq(addr_q[:12]),
            # The hard APB outputs arrive late in the fabric cycle. Register
            # the access phase before driving wide device write-enable cones.
            commit.eq(access_q & wait),
            self.fifo.req_enable.eq(self.req_enable),
            # The pulse catcher must gate REQ as well as the counter. Otherwise
            # two CPU-clock ACK pulses can merge before the next fabric edge.
            self.fifo.ack_tx.eq(level_tx),
            self.fifo.ack_rx.eq(level_rx),
            cpu.dma_req.eq(Cat(self.fifo.req_tx, self.fifo.req_rx,
                              self.audio.tx_req, self.audio.rx_req,
                              sd.tx_req if sd is not None else 0, sd.rx_req if sd is not None else 0, Constant(0, 2))),
            cpu.interrupt.eq(Cat(self.irq_pending & self.irq_enable, Constant(0, 1),
                                 self.audio.tx_irq, self.audio.rx_irq, Constant(0, 3),
                                 self.board.irq, Constant(0, 8))),
            self.audio.off.eq(off), self.audio.write.eq(write_q), self.audio.wdata.eq(wdata_q),
            self.audio.commit.eq(commit),
            self.board.off.eq(off),
            self.board.write.eq(write_q),
            self.board.wdata.eq(wdata_q),
            self.sense.off.eq(off),
            self.sense.write.eq(write_q),
            self.sense.wdata.eq(wdata_q),
            level_tx.eq(catch_tx | cpu.dma_ack[0]),
            level_rx.eq(catch_rx | cpu.dma_ack[1]),
        ]
        sysinfo = addr_q[12:] == (amap.SYSINFO_BASE >> 12)
        irq = addr_q[12:] == (amap.IRQ_BASE >> 12)
        ddr = addr_q[12:] == (amap.DDR_CSR_BASE >> 12)
        dma = addr_q[12:] == (amap.DMA_SVC_BASE >> 12)
        ws = addr_q[12:] == (amap.WS2812_BASE >> 12)
        board = addr_q[12:] == (amap.IO_BASE >> 12)
        sense = addr_q[12:] == (amap.SENSE_BASE >> 12)
        audio = addr_q[12:] == (amap.AUDIO_BASE >> 12)
        mic = addr_q[12:] == (amap.MIC_BASE >> 12)
        sd_page = addr_q[12:] == (amap.SD_BASE >> 12)
        sd_ok = sd.ok if sd is not None else 0
        sd_rdata = sd.rdata if sd is not None else 0
        video_page = addr_q[12:] == (amap.VIDEO_BASE >> 12)
        video_ok = video.ok if video is not None else 0
        video_rdata = video.rdata if video is not None else 0
        legal = (addr_q[:2] == 0) & (~write_q | (strb_q == 0xf))
        # Device entries are contiguous 32-byte records. Decode the record
        # index rather than OR-ing every individual address into error_q.
        sys_ro = _onehot(off,(0,4,8,0x20,0x24)) | ((off[5:]>=2) & (off[5:]<2+amap.DEVICE_COUNT))
        irq_ro = _onehot(off, (0x00, 0x04, 0x08, 0x10, 0x20))
        dma_ro = _onehot(off, (0x00, 0x04, 0x08, 0x20, 0x24, 0x28, 0x2c))
        status = Cat(self.fifo.empty, self.fifo.full, self.fifo.overflow, self.fifo.underflow)
        sys_data, irq_data, dma_data, ddr_data = (Signal(32) for _ in range(4))
        self.comb += [
            sys_data.eq(0), irq_data.eq(0), dma_data.eq(0), ddr_data.eq(0),
            Case(off, {
                0: sys_data.eq(amap.SYSINFO_ID), 4: sys_data.eq(amap.ABI_VERSION),
                8: sys_data.eq(amap.SYSINFO_CAP), 0x20: sys_data.eq(amap.DEVICE_COUNT),
                0x24: sys_data.eq(32),
                **{0x40+4*i: sys_data.eq(v) for i,v in enumerate(amap.LOOPBACK_EP)},
                **{0x60+4*i: sys_data.eq(v) for i,v in enumerate(amap.DMAC_EP)},
                **{0x80+4*i: sys_data.eq(v) for i,v in enumerate(amap.WS2812_EP)},
                **{0xa0+4*i: sys_data.eq(v) for i,v in enumerate(amap.IO_EP)},
                **{0xc0+4*i: sys_data.eq(v) for i,v in enumerate(amap.SENSE_EP)},
                **{0xe0+4*i: sys_data.eq(v) for i,v in enumerate(amap.AUDIO_EP)},
                **{0x100+4*i: sys_data.eq(v) for i,v in enumerate(amap.MIC_EP)},
                **{0x160+4*i: sys_data.eq(v) for i,v in enumerate(amap.SPI_EP)},
                **{0x180+4*i: sys_data.eq(v) for i,v in enumerate(amap.I2C_EP)},
                **{0x140+4*i: sys_data.eq(v) for i,v in enumerate(amap.SD_EP)},
                **{0x120+4*i: sys_data.eq(v) for i,v in enumerate(amap.VIDEO_EP)},
            }),
            Case(off, {
                0: irq_data.eq(amap.IRQ_ID),4: irq_data.eq(amap.ABI_VERSION),
                8: irq_data.eq(amap.CAP_EXT_IRQ),0x10: irq_data.eq(self.irq_pending),
                0x14: irq_data.eq(self.irq_pending),0x18: irq_data.eq(self.irq_enable),
                0x20: irq_data.eq(1),
            }),
            Case(off, {offset: ddr_data.eq(value) for offset,value in diagnostics.items()}),
            Case(off, {
                0: dma_data.eq(amap.DMA_SVC_ID),4: dma_data.eq(amap.ABI_VERSION),
                8: dma_data.eq(amap.DMA_SVC_CAP),0x0c: dma_data.eq(self.req_enable<<1),
                0x10: dma_data.eq(status),0x20: dma_data.eq(self.fifo.level),
                0x24: dma_data.eq(amap.LOOPBACK_DEPTH),0x28: dma_data.eq(amap.REQ_MAP),
                0x2c: dma_data.eq(Cat(self.ack_tx,self.ack_rx)),
            }),
        ]
        # Pages are disjoint. A balanced OR avoids the old priority chain
        # through every device before the registered APB response.
        pages = [
            (legal & sd_page & sd_ok,sd_rdata),
            (legal & sysinfo & ~write_q & sys_ro,sys_data),
            (legal & audio & self.audio.tx_ok,self.audio.tx_rdata),
            (legal & mic & self.audio.rx_ok,self.audio.rx_rdata),
            (legal & video_page & video_ok,video_rdata),
            (legal & sense & self.sense.ok,self.sense.rdata),
            (legal & ws & self.board.ws_ok,self.board.ws_rdata),
            (legal & board & self.board.io_ok,self.board.io_rdata),
            (legal & irq & ((~write_q & (irq_ro | (off==0x14) | (off==0x18))) |
                (write_q & (off==0x14)) | (write_q & (off==0x18) & (wdata_q[1:]==0))),irq_data),
            (legal & ddr & ~write_q,ddr_data),
            (legal & dma & ((~write_q & (dma_ro | (off==0x0c) | (off==0x10))) |
                (write_q & (off==0x0c) & (wdata_q[2:]==0)) |
                (write_q & (off==0x10) & ((wdata_q & ~0xc)==0))),dma_data),
        ]
        # Page-local readback registers cut the device decode from the final
        # response mux. CSR reads are observations; write admission/commit
        # still use the current device status on the execution cycle.
        words=[]
        for valid,value in pages:
            sampled=Signal(32)
            selected=Signal()
            sampled.attr.add(('syn_preserve', 1))
            self.sync += [sampled.eq(value), selected.eq(valid)]
            # Register data and admission independently: a deep page decode
            # must not become the synchronous RESET of every read-data bit.
            words.append(Mux(selected,sampled,0))
        while len(words)>1:
            words=[words[i] | words[i+1] if i+1<len(words) else words[i]
                   for i in range(0,len(words),2)]
        self.comb += [read_data.eq(words[0]), error.eq(~reduce(or_,[valid for valid,_ in pages]))]
        control_q,sticky_q,w1c_q,irq_enable_q=(Signal() for _ in range(4))
        self.sync += [
            control_q.eq(legal & dma & (off==0x0c) & write_q & (wdata_q[2:]==0)),
            sticky_q.eq(legal & dma & (off==0x10) & write_q & ((wdata_q & ~0xc)==0)),
            w1c_q.eq(legal & irq & (off==0x14) & write_q & wdata_q[0]),
            irq_enable_q.eq(legal & irq & (off==0x18) & write_q & (wdata_q[1:]==0)),
        ]
        control,sticky,w1c=commit & control_q,commit & sticky_q,commit & w1c_q
        self.comb += [
            self.fifo.clear.eq(control & wdata_q[0]),
            self.fifo.clear_overflow.eq(sticky & wdata_q[2]),
            self.fifo.clear_underflow.eq(sticky & wdata_q[3]),
            self.board.ws_selected.eq(legal & ws),
            self.board.io_selected.eq(legal & board),
            self.board.commit.eq(commit),
            self.sense.selected.eq(legal & sense),
            self.sense.commit.eq(commit),
            self.audio.tx_selected.eq(legal & audio),
            self.audio.rx_selected.eq(legal & mic),
        ]
        if sd is not None:
            self.comb += [sd.off.eq(off), sd.write.eq(write_q), sd.wdata.eq(wdata_q),
                          sd.commit.eq(commit), sd.selected.eq(legal & sd_page)]
        if video is not None:
            self.comb += [video.off.eq(off), video.write.eq(write_q), video.wdata.eq(wdata_q),
                          video.commit.eq(commit), video.selected.eq(legal & video_page)]
        self.sync += [
            If(~cpu.apb_select,
                wait.eq(0),
                access_q.eq(0),
            ).Elif(~cpu.apb_enable,
                addr_q.eq(cpu.apb_addr),
                wdata_q.eq(cpu.apb_wdata),
                write_q.eq(cpu.apb_write),
                strb_q.eq(cpu.apb_strb),
                wait.eq(1),
                access_q.eq(0),
            ).Elif(wait & ~access_q,
                access_q.eq(1),
            ).Elif(wait & (~sd_page | ~legal | (sd.complete if sd is not None else 1)),
                wait.eq(0),
                access_q.eq(0),
                cpu.apb_data.eq(Mux(sd_page & legal,sd.rdata if sd is not None else 0,read_data)),
                cpu.apb_slverr.eq(Mux(sd_page & legal,sd.error if sd is not None else 1,error_q)),
                If(control, self.req_enable.eq(wdata_q[1])),
                If(commit & irq_enable_q,
                   self.irq_enable.eq(wdata_q[0])),
            ),
            If(self.fifo.clear,
                self.ack_tx.eq(0), self.ack_rx.eq(0),
            ).Else(
                If(level_tx & ~ack_q[0], self.ack_tx.eq(self.ack_tx + 1)),
                If(level_rx & ~ack_q[1], self.ack_rx.eq(self.ack_rx + 1)),
            ),
            ack_q[0].eq(level_tx),
            ack_q[1].eq(level_rx),
            If(self.fifo.clear, self.irq_pending.eq(0))
            .Elif(self.fifo.irq_set, self.irq_pending.eq(1))
            .Elif(w1c, self.irq_pending.eq(0)),
        ]
