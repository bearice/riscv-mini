"""AE350 APB control and fixed-address AHB audio FIFO endpoints.

The data phase, rather than DMA_ACK, commits a sample. REQ/ACK only requests
one beat; the platform supplies pulse-caught ACK levels from the hard clock.
All protocol state and FIFOs run on sys with rational clock enables.
"""
from migen import Signal, Record, If, Case, Cat, Mux, FSM, NextState, NextValue
from litex.gen import LiteXModule
from litex.soc.interconnect.ahb import AHBInterface

from gateware.devices.audio.core import AudioCore
from gateware.devices.audio.clock import AudioDDS
from gateware.devices.microphone.core import MicrophoneCore
from gateware.machines.mega138k_ae350 import address_map as amap


class AudioData(LiteXModule):
    """One word, one direction, bounded ERROR on invalid/full/empty access."""
    def __init__(self, bus, address, write):
        self.valid = Signal()
        self.data = Signal(32)
        self.transfer = Signal()
        self.rejected = Signal()
        bad = Signal()
        result = Signal(32)
        self.comb += bus.rdata.eq(result)
        fsm = FSM(reset_state='IDLE')
        self.submodules.fsm = fsm
        capture = [NextValue(bad, (bus.addr != address) | (bus.size != 2) |
                                  (bus.write != int(write))), NextState('WORK')]
        fsm.act('IDLE', bus.readyout.eq(1), If(bus.trans[1], *capture))
        fsm.act('WORK',
            If(bad | ~self.valid,
                self.rejected.eq(1), NextState('ERROR_WAIT')
            ).Else(
                self.transfer.eq(1), NextValue(result, self.data), NextState('DONE')))
        fsm.act('ERROR_WAIT', bus.resp.eq(1), NextState('ERROR'))
        fsm.act('ERROR', bus.readyout.eq(1), bus.resp.eq(1),
                If(bus.trans[1], *capture).Else(NextState('IDLE')))
        fsm.act('DONE', bus.readyout.eq(1),
                If(bus.trans[1], *capture).Else(NextState('IDLE')))


class FabricAudio(LiteXModule):
    def __init__(self, clock_hz=amap.FABRIC_HZ, fifo_depth=512, sample_rate=48000):
        self.tx_pads = Record([('bck', 1), ('ws', 1), ('din', 1), ('pa_en', 1)])
        self.rx_pads = Record([('data', 1), ('bck', 1), ('lr', 1), ('ws', 1)])
        self.tx_bus = AHBInterface(data_width=32, address_width=32)
        self.rx_bus = AHBInterface(data_width=32, address_width=32)
        self.off = Signal(12)
        self.write = Signal()
        self.wdata = Signal(32)
        self.commit = Signal()
        self.tx_selected = Signal()
        self.rx_selected = Signal()
        self.tx_ok = Signal()
        self.rx_ok = Signal()
        self.tx_rdata = Signal(32)
        self.rx_rdata = Signal(32)
        self.tx_irq = Signal()
        self.rx_irq = Signal()
        self.tx_req = Signal()
        self.rx_req = Signal()
        self.tx_ack = Signal()
        self.rx_ack = Signal()
        self.clock = AudioDDS(clock_hz, sample_rate)
        self.tx = AudioCore(self.tx_pads, half_period=1, fifo_depth=fifo_depth,
                            clock_enable=self.clock.dac_tick)
        self.rx = MicrophoneCore(self.rx_pads, half_period=1, snapshot_samples=fifo_depth,
                                 clock_enable=self.clock.mic_tick)
        self.tx_data = AudioData(self.tx_bus, amap.AUDIO_DATA, True)
        self.rx_data = AudioData(self.rx_bus, amap.MIC_DATA, False)
        tx_control = Signal(3, reset=2)  # run, mute, dma; clear is write-only bit3
        rx_control = Signal(4)  # enable, right, stream, dma; arm/clear bits4/5
        tx_clear = Signal()
        rx_clear = Signal()
        rx_arm = Signal()
        tx_hold = Signal()
        rx_hold = Signal()
        tx_pending = Signal(2)
        rx_pending = Signal(3)
        tx_mask = Signal(2)
        rx_mask = Signal(3)
        tx_errors = Signal(2)
        rx_errors = Signal(3)
        seen_under = Signal(32)
        seen_over = Signal(32)
        seen_done = Signal()
        tx_events = Signal(2)
        rx_events = Signal(3)
        tx_go = self.commit & self.tx_selected & self.tx_ok & self.write
        rx_go = self.commit & self.rx_selected & self.rx_ok & self.write
        self.comb += [
            tx_clear.eq(tx_go & (self.off == 0x0c) & self.wdata[3]),
            rx_clear.eq(rx_go & (self.off == 0x0c) & self.wdata[5]),
            self.tx.run.eq(tx_control[0]), self.tx.mute.eq(tx_control[1]),
            self.tx.clear.eq(tx_clear),
            self.tx.pio_stb.eq(self.tx_data.transfer), self.tx.pio_data.eq(self.tx_bus.wdata),
            self.tx_data.valid.eq(self.tx.writable & ~tx_clear),
            self.rx.enable.eq(rx_control[0]), self.rx.right.eq(rx_control[1]),
            self.rx.stream.eq(rx_control[2]), self.rx.clear.eq(rx_clear), self.rx.arm.eq(rx_arm),
            self.rx.pop.eq(self.rx_data.transfer), self.rx_data.data.eq(self.rx.sample),
            self.rx_data.valid.eq((self.rx.level != 0) & ~rx_clear & ~rx_arm & rx_control[0]),
            self.tx_req.eq(tx_control[2] & self.tx.writable & ~tx_hold & ~self.tx_ack & ~tx_clear),
            self.rx_req.eq(rx_control[3] & (self.rx.level != 0) & ~rx_hold & ~self.rx_ack &
                           ~rx_clear & ~rx_arm & rx_control[0]),
            tx_events.eq(Cat((self.tx.underruns != seen_under) & ~tx_clear, self.tx_data.rejected)),
            rx_events.eq(Cat((self.rx.overruns != seen_over) & ~rx_clear & ~rx_arm,
                             self.rx.done & ~seen_done, self.rx_data.rejected)),
            self.tx_irq.eq((tx_pending & tx_mask) != 0),
            self.rx_irq.eq((rx_pending & rx_mask) != 0),
        ]
        tx_read = {
            0: amap.AUDIO_ID, 4: amap.ABI_VERSION, 8: amap.AUDIO_CAP,
            0x0c: tx_control, 0x10: Cat(self.tx.writable, self.tx.pa_en, self.tx_req, self.tx_ack, tx_hold),
            0x14: tx_pending, 0x18: tx_mask, 0x1c: tx_errors,
            0x20: sample_rate, 0x24: self.tx.level, 0x28: fifo_depth,
            0x2c: self.tx.frames, 0x30: self.tx.played, 0x34: self.tx.underruns,
            0x38: self.tx.overruns, 0x3c: self.tx.last_sample, 0x40: amap.AUDIO_REQ,
            0x44: amap.AUDIO_DMA_SEL,
        }
        rx_read = {
            0: amap.MIC_ID, 4: amap.ABI_VERSION, 8: amap.MIC_CAP,
            0x0c: rx_control, 0x10: Cat(self.rx.busy, self.rx.done, self.rx.activity, self.rx_req, self.rx_ack, rx_hold),
            0x14: rx_pending, 0x18: rx_mask, 0x1c: rx_errors,
            0x20: sample_rate, 0x24: self.rx.level, 0x28: fifo_depth,
            0x2c: self.rx.samples, 0x30: self.rx.captured, 0x34: self.rx.overruns,
            0x38: self.rx.last_sample, 0x3c: self.rx.activity,
            0x40: amap.MIC_REQ, 0x44: self.rx.accepted, 0x48: amap.MIC_DMA_SEL,
        }
        self.comb += [
            Case(self.off, {**{k: self.tx_rdata.eq(v) for k, v in tx_read.items()},
                            'default': self.tx_rdata.eq(0)}),
            Case(self.off, {**{k: self.rx_rdata.eq(v) for k, v in rx_read.items()},
                            'default': self.rx_rdata.eq(0)}),
            self.tx_ok.eq((~self.write & (Cat(*[self.off == k for k in tx_read]) != 0)) |
                (self.write & (((self.off == 0x0c) & (self.wdata < 16)) |
                  (((self.off == 0x14) | (self.off == 0x18)) & (self.wdata < 4))))),
            self.rx_ok.eq((~self.write & (Cat(*[self.off == k for k in rx_read]) != 0)) |
                (self.write & (((self.off == 0x0c) & (self.wdata < 64) &
                   (~self.wdata[4] | (self.wdata[0] & ~self.rx.busy)) &
                   (~self.rx.busy | ~self.wdata[0] | self.wdata[5] |
                    (self.wdata[1:3] == rx_control[1:3]))) |
                  (((self.off == 0x14) | (self.off == 0x18)) & (self.wdata < 8))))),
        ]
        self.sync += [
            rx_arm.eq(rx_go & (self.off == 0x0c) & self.wdata[4]),
            tx_hold.eq(self.tx_ack), rx_hold.eq(self.rx_ack),
            seen_under.eq(self.tx.underruns), seen_over.eq(self.rx.overruns), seen_done.eq(self.rx.done),
            If(tx_go & (self.off == 0x0c), tx_control.eq(self.wdata[:3])),
            If(rx_go & (self.off == 0x0c), rx_control.eq(self.wdata[:4])),
            If(tx_go & (self.off == 0x18), tx_mask.eq(self.wdata[:2])),
            If(rx_go & (self.off == 0x18), rx_mask.eq(self.wdata[:3])),
            If(tx_clear, tx_pending.eq(0), tx_errors.eq(0), seen_under.eq(0)).Else(
                tx_pending.eq((tx_pending & ~Mux(tx_go & (self.off == 0x14), self.wdata[:2], 0)) | tx_events),
                tx_errors.eq(tx_errors | tx_events)),
            If(rx_clear | rx_arm | ~rx_control[0],
                rx_pending.eq(0), rx_errors.eq(0), seen_over.eq(0), seen_done.eq(0)
            ).Else(
                rx_pending.eq((rx_pending & ~Mux(rx_go & (self.off == 0x14), self.wdata[:3], 0)) | rx_events),
                rx_errors.eq(rx_errors | (rx_events & 5))),
        ]
