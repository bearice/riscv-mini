"""Verify the exact mode/bit order/manual-CS contract used by M2 firmware."""
from migen import Module, Record
from migen.sim import run_simulation
from litex.soc.cores.spi import SPIMaster


def check(width, mode, words):
    dut=Module()
    pads=Record(SPIMaster.pads_layout)
    dut.submodules.spi=spi=SPIMaster(pads,width,48e6,6e6,with_csr=False,mode=mode)

    def bench():
        yield spi.cs_mode.eq(1)
        yield spi.cs.eq(0)
        yield
        yield
        assert (yield pads.cs_n)==1
        yield spi.cs.eq(1)
        yield
        yield
        for value,bits in words:
            yield spi.length.eq(bits)
            yield spi.mosi.eq(value)
            yield spi.start.eq(1)
            yield
            yield spi.start.eq(0)
            sampled=[]
            previous=0
            for cycle in range(2000):
                assert (yield pads.cs_n)==0, 'CS changed inside held transaction'
                clock=(yield pads.clk)
                if clock and not previous: sampled.append((yield pads.mosi))
                previous=clock
                if cycle>2 and (yield spi.done): break
                yield
            else: raise AssertionError('SPI transfer did not finish')
            expected=[(value>>i)&1 for i in range(bits-1,-1,-1)]
            assert sampled==expected, (sampled,expected)
            assert (yield pads.clk)==0
        yield spi.cs.eq(0)
        yield
        yield
        assert (yield pads.cs_n)==1
        yield spi.length.eq(width+1)
        yield spi.start.eq(1)
        yield
        yield spi.start.eq(0)
        yield
        yield
        assert (yield spi.error)==1
        assert (yield pads.clk)==0 and (yield pads.cs_n)==1

    run_simulation(dut,bench())


if __name__=='__main__':
    check(16,'aligned',[(0x36,8),(0x70,8),(0xf800,16),(0x07e0,16),(0x001f,16)])
    check(8,'raw',[(0x40,8),(0xff,8),(0x95,8),(0xaa,8)])
    print('SPI simulation PASS: MSB first, 8/16 bits, held CS, idle clock, invalid length')
