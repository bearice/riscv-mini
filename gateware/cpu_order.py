"""Order CPU MMIO accesses after posted DDR writes without changing Wishbone."""
from migen import Signal, If
from litex.gen import LiteXModule
from litex.soc.interconnect import wishbone


class CPUOrderBridge(LiteXModule):
    def __init__(self, fabric, idle):
        self.cpu = cpu = wishbone.Interface(data_width=32,address_width=32,addressing='word')
        self.drain = Signal()
        outside = (cpu.adr < (0x40000000 >> 2)) | (cpu.adr >= (0x48000000 >> 2))
        admitted=Signal()
        allowed = ~outside | idle | admitted
        self.sync += If(~cpu.cyc | fabric.ack | fabric.err,admitted.eq(0)).Elif(
            cpu.cyc & cpu.stb & outside & idle,admitted.eq(1))
        self.comb += [self.drain.eq(cpu.cyc & cpu.stb & outside),
            # Retain bus ownership while draining; once admitted, never retract
            # a peripheral strobe because another in-flight request changes idle.
            fabric.cyc.eq(cpu.cyc),fabric.stb.eq(cpu.stb & allowed),
            cpu.ack.eq(fabric.ack & allowed & cpu.cyc),
            cpu.err.eq(fabric.err & allowed & cpu.cyc),cpu.dat_r.eq(fabric.dat_r)]
        self.comb += [getattr(fabric,n).eq(getattr(cpu,n))
                      for n in ('adr','dat_w','sel','we','cti','bte')]
