"""Fan PWM and the GW5AST die-temperature ADC.

The fan supply is a P-channel MOSFET whose source is +5 V. The pad reaches
that gate through 15 ohms and is open-drain: a duty of 100 pulls the gate
low for the whole period, and a duty of 0 releases it. Turning the
transistor off still needs the gate pulled up to +5 V, which this sheet
does not show. The tachometer is an open-drain input with its pull-up on
the board. A stopped rotor, or a gap longer than half a second, reads as
period 0. Two pulses per revolution is the usual fan, so software turns a
period into RPM with fabric_hz * 30 / period. The ADC is the lower-right
converter in temperature mode: the vendor result is degrees C in units of
1/4, so the page publishes that sample times 250 as millidegrees. The
typical full-scale 730 and offset -1180 are the primitive's own constants.
Simulation does not contain the converter; it returns 25.00 C when asked.
"""
from migen import *
from migen.fhdl.specials import Special, SPECIAL_INPUT, SPECIAL_OUTPUT
from litex.gen import LiteXModule

from gateware.machines.mega138k_ae350 import address_map as amap


FAN_HZ = 25_000
FAN_PERIOD = amap.FABRIC_HZ // FAN_HZ
TEMP_CODE = 100  # 25.00 C in the vendor's quarter-degree sample
SAMPLE_GUARD = amap.FABRIC_HZ // 10
TACH_DIV = 16
TACH_MIN = 800 // TACH_DIV  # ignore edges closer than 10 us
TACH_TIMEOUT = (amap.FABRIC_HZ // 2) // TACH_DIV


class _TempSim(Module):
    def __init__(self, req, ready, value):
        self.sync += [
            If(req & ~ready, value.eq(TEMP_CODE), ready.eq(1)),
            If(~req, ready.eq(0)),
        ]


class TempAdc(Special):
    def __init__(self, clk, rst_n, req, ready, value):
        Special.__init__(self)
        self.clk = wrap(clk)
        self.rst_n = wrap(rst_n)
        self.req = wrap(req)
        self.ready = wrap(ready)
        self.value = wrap(value)

    def iter_expressions(self):
        yield self, "clk", SPECIAL_INPUT
        yield self, "rst_n", SPECIAL_INPUT
        yield self, "req", SPECIAL_INPUT
        yield self, "ready", SPECIAL_OUTPUT
        yield self, "value", SPECIAL_OUTPUT

    @staticmethod
    def lower(dr):
        return _TempSim(dr.req, dr.ready, dr.value)


class _GowinTempAdc(Module):
    def __init__(self, clk, rst_n, req, ready, value):
        self.specials += Instance("ADCLRC",
            p_VSEN_CTL=0b001,  # adct, after adcv
            p_VSEN_CTL_SEL=0,
            p_ADC_MODE=0,
            p_CLK_SEL=0,       # PIO clock, not a fabric net
            p_PIOCLK_SEL=0,    # on-chip ADC oscillator, about 2.5 MHz
            p_DIV_CTL=0,
            p_SAMPLE_CNT_SEL=4,
            p_RATE_CHANGE_CTRL=4,
            i_VSENCTL=Constant(0b001, 3),
            i_FSCAL_VALUE=730,
            i_OFFSET_VALUE=Constant(-1180, 12),
            i_ADCEN=1,
            i_CLK=clk,
            i_DRSTN=rst_n,
            i_ADCREQI=req,
            i_ADCINBK2A=0, i_ADCINBK2B=0, i_ADCINBK3A=0, i_ADCINBK3B=0,
            i_ADCINBK4A=0, i_ADCINBK4B=0, i_ADCINBK5A=0, i_ADCINBK5B=0,
            o_ADCRDY=ready,
            o_ADCVALUE=value)


class GowinTempAdc:
    @staticmethod
    def lower(dr):
        return _GowinTempAdc(dr.clk, dr.rst_n, dr.req, dr.ready, dr.value)


class BoardSense(LiteXModule):
    def __init__(self):
        self.pull = Signal(reset=1)
        self.tach = Signal()
        self.off = Signal(12)
        self.write = Signal()
        self.wdata = Signal(32)
        self.selected = Signal()
        self.commit = Signal()
        self.rdata = Signal(32)
        self.ok = Signal()
        self.ok.attr.add(("syn_keep", 1))

        percent = Signal(7, reset=100)
        count = Signal(max=FAN_PERIOD)
        high = Signal(max=FAN_PERIOD + 1)
        adc_rst_n = Signal(reset=0)
        req = Signal()
        adc_ready = Signal()
        adc_value = Signal(14)
        ready_meta = Signal()
        ready_sync = Signal()
        ready_q = Signal()
        value_meta = Signal(14)
        value_sync = Signal(14)
        raw = Signal((14, True))
        millideg = Signal((32, True))
        busy = Signal()
        valid = Signal()
        error = Signal()
        guard = Signal(max=SAMPLE_GUARD + 1)
        start = Signal()
        tach_meta = Signal()
        tach_sync = Signal()
        tach_prev = Signal()
        div = Signal(max=TACH_DIV)
        span = Signal(max=TACH_TIMEOUT + 1)
        period = Signal(32)
        stopped = Signal(reset=1)
        rise = Signal()
        tick = Signal()
        self.specials += TempAdc(0, adc_rst_n, req, adc_ready, adc_value)
        self.comb += [
            high.eq(percent * (FAN_PERIOD // 100)),
            self.pull.eq(count < high),
            rise.eq(tach_sync & ~tach_prev),
            tick.eq(div == TACH_DIV - 1),
            millideg.eq(raw * 250),
            start.eq(self.commit & self.selected & self.ok & self.write &
                     (self.off == 0x0c) & (self.wdata == 1)),
        ]
        reads = {
            0x00: amap.SENSE_ID, 0x04: amap.ABI_VERSION, 0x08: amap.SENSE_CAP,
            0x10: Cat(valid, busy), 0x1c: error, 0x20: percent,
            0x24: raw, 0x28: millideg, 0x2c: period,
        }
        hit = Signal()
        self.comb += [
            hit.eq(Cat(*[self.off == item for item in reads]) != 0),
            self.ok.eq((~self.write & hit) |
                       (self.write & (self.off == 0x0c) & (self.wdata == 1) & ~busy) |
                       (self.write & (self.off == 0x20) & (self.wdata <= 100))),
            Case(self.off, {**{offset: self.rdata.eq(value) for offset, value in reads.items()},
                            "default": self.rdata.eq(0)}),
        ]
        self.sync += [
            If(count == FAN_PERIOD - 1, count.eq(0)).Else(count.eq(count + 1)),
            tach_meta.eq(self.tach),
            tach_sync.eq(tach_meta),
            tach_prev.eq(tach_sync),
            If(tick, div.eq(0)).Else(div.eq(div + 1)),
            If(rise,
                If(~stopped & (span >= TACH_MIN), period.eq((span + 1) * TACH_DIV)),
                span.eq(0),
                stopped.eq(0),
            ).Elif(stopped,
                span.eq(0),
            ).Elif(tick & (span == TACH_TIMEOUT),
                period.eq(0),
                stopped.eq(1),
            ).Elif(tick,
                span.eq(span + 1),
            ),
            adc_rst_n.eq(1),
            ready_meta.eq(adc_ready),
            ready_sync.eq(ready_meta),
            value_meta.eq(adc_value),
            value_sync.eq(value_meta),
            If(self.commit & self.selected & self.write & self.ok & (self.off == 0x20),
               percent.eq(self.wdata[:7])),
            If(start,
                busy.eq(1), valid.eq(0), error.eq(0), req.eq(1), guard.eq(0)
            ).Elif(busy & ready_sync & ~ready_q,
                raw.eq(value_sync), valid.eq(1), busy.eq(0), req.eq(0)
            ).Elif(busy & (guard == SAMPLE_GUARD),
                error.eq(1), busy.eq(0), req.eq(0)
            ).Elif(busy,
                guard.eq(guard + 1)),
            ready_q.eq(ready_sync),
        ]
