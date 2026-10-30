# Copyright (c) 2017-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2017 Pierre-Olivier Vauboin <po@lambdaconcept.com>
# SPDX-License-Identifier: BSD-2-Clause
"""LiteSDCard PHY assembly with GW5A registered-input sampling alignment.

Assembly follows locked LiteSDCard 17718d9258ac2dd62ac23e2eecd7ac613a050986.
Command/data-write engines remain upstream; the local data reader registers
its byte delimiters and timeout terminal flag. The input enable selects the registered
pad sample one mem100 cycle later than the upstream receive-enable phase.
"""
from migen import *
from litex.gen import LiteXModule, Reduce
from litex.soc.interconnect.csr import CSRStorage, CSRStatus, CSRField, CSRConstant
from litex.soc.interconnect import stream
from litesdcard.crc import CRC16
from litesdcard.common import (SD_PHY_SPEED_4X,SD_PHY_SPEED_8X,
    SDCARD_STREAM_STATUS_OK,SDCARD_STREAM_STATUS_CRCERROR,SDCARD_STREAM_STATUS_TIMEOUT)
from litesdcard.phy import (_sdpads_layout, SDPHYClocker, SDPHYInit, SDPHYCMDW,
    SDPHYCMDR, SDPHYDATAW, SDPHYR, SDPHYIOGen, SDPHYIOEmulator)


# From locked LiteSDCard 17718d9258ac2dd62ac23e2eecd7ac613a050986.
# Snapshot byte limits and register byte/timeout terminal flags. The timeout
# flag follows the loaded/decremented count without changing its expiry cycle.
# Sampling, CRC and clock-stop logic remain.
class SDPHYDATAR(LiteXModule):
    def __init__(self, sdpads_layout, data_width, sys_clk_freq, data_timeout):
        self.pads_in  = pads_in  = stream.Endpoint(sdpads_layout)
        self.pads_out = pads_out = stream.Endpoint(sdpads_layout)
        self.sink     = sink     = stream.Endpoint([("block_length", 10)])
        self.source   = source   = stream.Endpoint([("data", 8), ("status", 3), ("drop", 1)])
        self.stop     = Signal()

        self.timeout  = CSRStorage(32, reset=int(data_timeout*sys_clk_freq))

        # # #

        timeout     = Signal(32)
        timeout_zero = Signal(reset=1)
        count       = Signal(11)
        last_count  = Signal(11)
        drop_limit  = Signal((11,True))
        last_byte   = Signal()
        drop_byte   = Signal()
        crc_count   = Signal(max=17)
        crc_len     = Signal(max=17)
        crc_correct = Signal()
        crc_error   = Signal()
        data_done   = Signal()
        data_len    = Signal(max=9)
        data_count  = Signal(16)

        datar_source  = stream.Endpoint([("data", 8)])
        datar_reset   = Signal()
        datar_valid   = Signal()

        self.crc16 = crc16 = CRC16(pads_in.data.i, crc_count)

        self.comb += [
            crc16.reset.eq(datar_reset),
            data_done.eq(data_count == 0),
            crc16.enable.eq(datar_valid & ~data_done),
        ]

        self.sync += [
            If(crc16.reset,
                data_count.eq(sink.block_length * 8),
            ).Elif(crc16.enable,
                data_count.eq(data_count - data_len),
            )
        ]

        datar_cases = {}

        # SD_PHY_SPEED_1X.
        self.datar_1x = datar_1x = SDPHYR(sdpads_layout, data=True, data_width=1, skip_start_bit=True)
        self.comb += [
            datar_1x.reset.eq(datar_reset),
            pads_in.connect(datar_1x.pads_in),
        ]
        datar_cases["default"] = [
            datar_1x.source.connect(datar_source),
            crc_len.eq(2),
            data_len.eq(1),
            datar_valid.eq(datar_1x.converter.sink.valid),
            crc_correct.eq(crc16.data_pads_out[0] == pads_in.data.i[0]),
        ]

        # SD_PHY_SPEED_4X.
        if len(pads_in.data.i) >= 4:
            self.datar_4x = datar_4x = SDPHYR(sdpads_layout, data=True, data_width=4, skip_start_bit=True)
            self.comb += [
                datar_4x.reset.eq(datar_reset),
                pads_in.connect(datar_4x.pads_in),
            ]
            datar_cases[SD_PHY_SPEED_4X] = [
                datar_4x.source.connect(datar_source),
                crc_len.eq(8),
                data_len.eq(4),
                datar_valid.eq(datar_4x.converter.sink.valid),
                crc_correct.eq(crc16.data_pads_out[:4] == pads_in.data.i[:4]),
            ]

        # SD_PHY_SPEED_8X.
        if len(pads_in.data.i) >= 8:
            self.datar_8x = datar_8x = SDPHYR(sdpads_layout, data=True, data_width=8, skip_start_bit=True)
            self.comb += [
                datar_8x.reset.eq(datar_reset),
                pads_in.connect(datar_8x.pads_in),
            ]
            datar_cases[SD_PHY_SPEED_8X] = [
                datar_8x.source.connect(datar_source),
                crc_len.eq(16),
                data_len.eq(8),
                datar_valid.eq(datar_8x.converter.sink.valid),
                crc_correct.eq(crc16.data_pads_out[:8] == pads_in.data.i[:8]),
            ]

        self.comb += Case(data_width, datar_cases)

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            NextValue(count, 0),
            NextValue(crc_count, 0),
            If(sink.valid & pads_out.ready,
                pads_out.clk.eq(1),
                NextValue(timeout, self.timeout.storage),
                NextValue(timeout_zero, self.timeout.storage == 0),
                NextValue(last_count,sink.block_length+crc_len-1),
                NextValue(drop_limit,sink.block_length-1),
                NextValue(last_byte,0),NextValue(drop_byte,sink.block_length==0),
                NextValue(crc_error, 0),
                NextValue(datar_reset, 1),
                NextState("WAIT")
            )
        )
        fsm.act("WAIT",
            pads_out.clk.eq(1),
            NextValue(datar_reset, 0),
            If(datar_source.valid,
                NextState("DATA")
            ),
            NextValue(timeout, timeout - 1),
            NextValue(timeout_zero, timeout == 1),
            If(timeout_zero,
                sink.ready.eq(1),
                NextState("TIMEOUT")
            )
        )
        fsm.act("DATA",
            pads_out.clk.eq(1),
            source.valid.eq(datar_source.valid),
            source.status.eq(Mux(crc_error, SDCARD_STREAM_STATUS_CRCERROR, SDCARD_STREAM_STATUS_OK)),
            source.first.eq(count == 0),
            source.last.eq(last_byte), # registered for the current byte
            source.drop.eq(drop_byte), # registered CRC/payload boundary
            source.data.eq(datar_source.data),
            If(source.valid,
                If(source.ready,
                    datar_source.ready.eq(1),
                    NextValue(count, count + 1),
                    NextValue(last_byte,count==last_count-1),
                    NextValue(drop_byte,count>=drop_limit),
                    If(source.last,
                        sink.ready.eq(1),
                        If(sink.last,
                            NextValue(count, 0),
                            NextState("CLK8")
                        ).Else(
                            NextState("IDLE")
                        )
                    )
                ).Else(
                     self.stop.eq(1)
                )
            ),
            If(pads_in.valid & data_done & (crc_count < 16),
                NextValue(crc_count, crc_count + 1),
                If(~crc_correct,
                    NextValue(crc_error, 1),
                )
            ),
            NextValue(timeout, timeout - 1),
            NextValue(timeout_zero, timeout == 1),
            If(timeout_zero,
                sink.ready.eq(1),
                NextState("TIMEOUT")
            )
        )
        fsm.act("CLK8",
            pads_out.clk.eq(1),
            If(pads_out.ready,
                NextValue(count, count + 1),
                If(count == (8-1),
                    NextState("IDLE")
                )
            )
        )
        fsm.act("TIMEOUT",
            source.valid.eq(1),
            source.status.eq(SDCARD_STREAM_STATUS_TIMEOUT),
            source.last.eq(1),
            If(source.ready,
                NextState("IDLE")
            )
        )


class InputSampleEnable(LiteXModule):
    def __init__(self, clock):
        # Output pad clock and input pad data each have one SDR register.
        # Add one mem100 cycle to the receive-enable phase.
        self.ce = Signal()
        pad_clock, previous, edge = Signal(), Signal(), Signal()
        self.sync += [pad_clock.eq(~clock), previous.eq(pad_clock),
                      edge.eq(pad_clock & ~previous)]
        self.comb += self.ce.eq(edge)


class RegisteredSDPHYIO(SDPHYIOGen):
    def add_data_i_ce(self, clocker, sdpads):
        self.sample_enable = InputSampleEnable(clocker.clk)
        self.comb += sdpads.data_i_ce.eq(self.sample_enable.ce)


class SDPHY(LiteXModule):
    def __init__(self, pads, device, sys_clk_freq, cmd_timeout=10e-3, data_timeout=10e-3):
        use_emulator = hasattr(pads, "cmd_t") and hasattr(pads, "dat_t")
        self.card_detect = CSRStatus() # Assume SDCard is present if no cd pin.
        self.comb += self.card_detect.status.eq(getattr(pads, "cd", 0))

        pads_data_width = len(pads.dat_t) if use_emulator else len(pads.data)
        sdpads_layout = _sdpads_layout(pads_data_width)

        data_width = Signal(2)

        self.clocker = clocker = SDPHYClocker()
        self.init    = init    = SDPHYInit(sdpads_layout, with_reset=hasattr(pads, "rst"))
        self.cmdw    = cmdw    = SDPHYCMDW(sdpads_layout)
        self.cmdr    = cmdr    = SDPHYCMDR(sdpads_layout, sys_clk_freq, cmd_timeout, cmdw)
        self.dataw   = dataw   = SDPHYDATAW(sdpads_layout, data_width)
        self.datar   = datar   = SDPHYDATAR(sdpads_layout, data_width, sys_clk_freq, data_timeout)

        self.settings = CSRStorage(fields=[
            CSRField("data_width", size=2, offset=0, values=[
                ("0b00", "1-bit"),
                ("0b01", "4-bit"),
                ("0b10", "8-bit"),
            ], reset=SD_PHY_SPEED_4X), # Defaults to 4x speed for retro-compatibility.
        ])

        self.comb += data_width.eq(self.settings.fields.data_width)

        self.sdpads = sdpads = Record(sdpads_layout)

        if len(sdpads.data.i) >= 8:
            self.support_8x = CSRConstant(1)

        # IOs
        sdphy_cls = SDPHYIOEmulator if use_emulator else RegisteredSDPHYIO
        self.io = sdphy_cls(clocker, sdpads, pads)

        # Connect pads_out of submodules to physical pads ----------------------------------------
        self.comb += [
            sdpads.clk.eq(    Reduce("OR", [m.pads_out.clk     for m in [init, cmdw, cmdr, dataw, datar]])),
            sdpads.cmd.oe.eq( Reduce("OR", [m.pads_out.cmd.oe  for m in [init, cmdw, cmdr, dataw, datar]])),
            sdpads.cmd.o.eq(  Reduce("OR", [m.pads_out.cmd.o   for m in [init, cmdw, cmdr, dataw, datar]])),
            sdpads.data.oe.eq(Reduce("OR", [m.pads_out.data.oe for m in [init, cmdw, cmdr, dataw, datar]])),
            sdpads.data.o.eq( Reduce("OR", [m.pads_out.data.o  for m in [init, cmdw, cmdr, dataw, datar]])),
        ]
        for m in [init, cmdw, cmdr, dataw, datar]:
            self.comb += m.pads_out.ready.eq(self.clocker.ce)
        self.comb += self.clocker.clk_en.eq(sdpads.clk)

        # Connect physical pads to pads_in of submodules -------------------------------------------
        for m in [init, cmdw, cmdr, dataw, datar]:
            self.comb += m.pads_in.valid.eq(sdpads.data_i_ce)
            self.comb += m.pads_in.cmd.i.eq(sdpads.cmd.i)
            self.comb += m.pads_in.data.i.eq(sdpads.data.i)


        # Speed Throttling -------------------------------------------------------------------------
        self.comb += clocker.stop.eq(dataw.stop | datar.stop)

        # Card Reset -------------------------------------------------------------------------------
        if hasattr(self.io, "card_reset"):
            self.comb += self.io.card_reset.eq(init.card_reset)

        # IRQs -------------------------------------------------------------------------------------
        self.card_detect_irq = Signal() # Generate Card Detect IRQ on level change.
        card_detect_d = Signal()
        self.sync += card_detect_d.eq(self.card_detect.status)
        self.sync += self.card_detect_irq.eq(self.card_detect.status ^ card_detect_d)
