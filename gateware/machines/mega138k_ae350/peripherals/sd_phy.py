# Copyright (c) 2017-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2017 Pierre-Olivier Vauboin <po@lambdaconcept.com>
# SPDX-License-Identifier: BSD-2-Clause
"""LiteSDCard PHY assembly with GW5A registered-input sampling alignment.

Assembly follows locked LiteSDCard 17718d9258ac2dd62ac23e2eecd7ac613a050986.
Command/data engines remain upstream. The input enable selects the registered
pad sample one mem100 cycle later than the upstream receive-enable phase.
"""
from migen import *
from litex.gen import LiteXModule, Reduce
from litex.soc.interconnect.csr import CSRStorage, CSRStatus, CSRField, CSRConstant
from litesdcard.common import SD_PHY_SPEED_4X
from litesdcard.phy import (_sdpads_layout, SDPHYClocker, SDPHYInit, SDPHYCMDW,
    SDPHYCMDR, SDPHYDATAW, SDPHYDATAR, SDPHYIOGen, SDPHYIOEmulator)


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
