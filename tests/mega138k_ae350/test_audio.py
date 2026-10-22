"""Exercise physical serial pins and real AHB/APB transactions, including loss."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from migen import Signal
from migen.fhdl.specials import Memory
from migen.sim import run_simulation, passive
from gateware.machines.mega138k_ae350.peripherals.audio import FabricAudio
from gateware.machines.mega138k_ae350 import address_map as amap
from test_ae350_interconnect import xact


def main():
    d = FabricAudio(clock_hz=24_576_000, fifo_depth=8)
    serial_words = []
    values = [0x123456, 0x800000, 0x7fffff, 0xffffff]

    def control(tx, off, data=None):
        yield [d.tx_selected.eq(tx), d.rx_selected.eq(not tx), d.off.eq(off),
               d.write.eq(data is not None), d.wdata.eq(data or 0), d.commit.eq(0)]
        yield
        ok = yield d.tx_ok if tx else d.rx_ok
        value = yield d.tx_rdata if tx else d.rx_rdata
        yield d.commit.eq(1)
        yield
        yield d.commit.eq(0)
        yield
        return ok, value

    @passive
    def mic():
        prev_bck=0; prev_ws=1; bit=31; index=0; word=0
        while True:
            bck=(yield d.rx_pads.bck); ws=(yield d.rx_pads.ws)
            if not bck and prev_bck:
                if ws!=prev_ws:
                    bit=0
                    word=values[index%4]
                    if ws==0: index+=1
                else: bit+=1
                yield d.rx_pads.data.eq(((word>>(24-bit))&1) if 1<=bit<=24 else 1)
            prev_bck=bck; prev_ws=ws
            yield

    @passive
    def dac():
        prev=0; bits=[]; ws_prev=0
        while True:
            bck=(yield d.tx_pads.bck); ws=(yield d.tx_pads.ws)
            if bck and not prev:
                if ws != ws_prev:
                    if len(bits)==16: serial_words.append((ws_prev, int(''.join(map(str,bits)),2)))
                    bits=[]; ws_prev=ws
                bits.append((yield d.tx_pads.din))
            prev=bck
            yield

    def bench():
        assert amap.check()==[]
        assert (yield from control(True,0))[1]==amap.AUDIO_ID
        assert (yield from control(False,0))[1]==amap.MIC_ID
        assert not (yield from control(True,0xc,16))[0]
        assert not (yield from control(False,0xc,64))[0]
        words=[0x80017fff,0x1234fedc,0xa55a5aa5]
        for word in words:
            assert (yield from xact(d.tx_bus,True,amap.AUDIO_DATA,word))[0]==0
        assert (yield d.tx.level)==3
        assert (yield from xact(d.tx_bus,False,amap.AUDIO_DATA))[0]==1
        assert (yield from xact(d.tx_bus,True,amap.AUDIO_DATA+4,0))[0]==1
        assert (yield from xact(d.tx_bus,True,amap.AUDIO_DATA,0,size=0))[0]==1
        assert (yield d.tx.level)==3
        yield from control(True,0x18,1)
        yield from control(True,0xc,1)
        for _ in range(512*5): yield
        assert (yield d.tx.played)==3
        assert (yield d.tx.underruns)>0
        assert (yield d.tx_irq)
        assert (yield d.tx.last_sample)==0
        packed=[(r<<16)|l for (ws,r),(ws2,l) in zip(serial_words,serial_words[1:]) if ws==0 and ws2==1]
        assert words[1] in packed and words[2] in packed, packed
        assert packed.index(words[1])<packed.index(words[2])
        yield from control(True,0xc,10)
        assert not (yield d.tx_pads.pa_en)
        assert (yield d.tx.level)==0
        for i in range(8): assert (yield from xact(d.tx_bus,True,amap.AUDIO_DATA,i))[0]==0
        assert (yield from xact(d.tx_bus,True,amap.AUDIO_DATA,9))[0]==1
        yield from control(True,0xc,6)
        assert not (yield d.tx_req)
        yield from control(True,0xc,14)
        yield
        assert (yield d.tx_req)
        yield d.tx_ack.eq(1); yield; yield
        assert not (yield d.tx_req)
        assert (yield d.tx.level)==0, 'ACK must not commit data'
        yield d.tx_ack.eq(0); yield; yield; yield
        assert (yield d.tx_req)
        # Address HWDATA is deliberately different from data-phase HWDATA.
        b=d.tx_bus
        yield [b.addr.eq(amap.AUDIO_DATA),b.size.eq(2),b.write.eq(1),b.trans.eq(2),b.wdata.eq(0xdead),b.sel.eq(0xff)]
        yield
        yield [b.trans.eq(0),b.wdata.eq(0xcafefeed)]
        yield
        while not (yield b.readyout): yield
        yield
        assert (yield d.tx.fifo.dout)==0xcafefeed
        yield from control(True,0xc,10)

        # Arm and enable in the same command must work after the clock starts.
        assert (yield from control(False,0xc,17))[0]
        for _ in range(512*10):
            if (yield d.rx.done): break
            yield
        else: raise AssertionError('snapshot did not finish')
        assert (yield d.rx.captured)==8 and (yield d.rx.level)==8
        assert not (yield d.rx.overruns)
        assert not (yield from xact(d.rx_bus,True,amap.MIC_DATA,1))[0]==0
        got=[]
        for _ in range(8):
            resp, value=yield from xact(d.rx_bus,False,amap.MIC_DATA)
            assert resp==0; got.append(value)
        valid=[v | (0xff000000 if v&0x800000 else 0) for v in values]
        assert set(got)==set(valid), [hex(w) for w in got]
        assert (yield from xact(d.rx_bus,False,amap.MIC_DATA))[0]==1
        yield from control(False,0xc,1|4|16)
        for _ in range(512*14): yield
        assert (yield d.rx.overruns)>0 and (yield d.rx.busy)
        assert (yield d.rx.accepted)==8
        yield from control(False,0xc,1|4|8)
        assert (yield d.rx_req)
        for _ in range(8): assert (yield from xact(d.rx_bus,False,amap.MIC_DATA))[0]==0
        for _ in range(512*2): yield
        assert (yield d.rx.accepted)>8, 'stream must resume after overflow and drain'
        before=(yield d.rx.accepted)
        assert not (yield from control(False,0xc,1|2|4|8))[0], 'cannot change live channel'
        yield from control(False,0xc,32)
        assert not (yield d.rx_req) and not (yield d.rx.level)
        assert not (yield d.rx.busy)
        print('PASS physical PT8211/I2S decode, signed PCM24, snapshot, stream overflow/recovery, AHB errors/data-phase, REQ/ACK, stop')

    fragment=d.get_fragment()
    for m in fragment.specials:
        if isinstance(m,Memory):
            for p in m.ports:
                if p.dat_r is None: p.dat_r=Signal(m.width)
    run_simulation(fragment,[bench(),mic(),dac()])


if __name__=='__main__': main()
