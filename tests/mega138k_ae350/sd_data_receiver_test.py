"""Cycle equivalence of retimed RX and locked upstream, with real pad bits."""
import random
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from migen import Module,ClockDomain,Signal
from migen.sim import run_simulation
from migen.sim.core import Simulator
from litesdcard.phy import _sdpads_layout,SDPHYDATAR as Reference
from gateware.machines.mega138k_ae350.peripherals.sd_phy import SDPHYDATAR


def pad_words(data,lanes,corrupt):
    crc=[0]*lanes
    idle=255^((1<<lanes)-1)
    words=[idle]
    for value in data:
        for shift in range(8-lanes,-1,-lanes):
            word=(value>>shift)&((1<<lanes)-1)
            words.append(word|idle)
            for lane in range(lanes):
                feedback=((crc[lane]>>15)^((word>>lane)&1))&1
                crc[lane]=((crc[lane]<<1)&65535)^(0x1021 if feedback else 0)
    for bit in range(15,-1,-1):
        word=sum(((value>>bit)&1)<<lane for lane,value in enumerate(crc))
        if corrupt and bit==7:word^=1
        words.append(word|idle)
    return words+[255]


class ReceiverTest(unittest.TestCase):
    def check_case(self,speed,size,corrupt=False,timeout_case=False):
        print('RX case',speed,size,corrupt,timeout_case,flush=True)
        m=Module();m.clock_domains.cd_sys=ClockDomain('sys')
        mode=Signal(2,reset=speed);length=Signal(10,reset=size)
        valid,ready,pad_valid=Signal(),Signal(),Signal()
        pad_data=Signal(8,reset=255)
        timeout=64 if timeout_case else 30000
        receivers=[]
        for name,cls in (('local',SDPHYDATAR),('reference',Reference)):
            receiver=cls(_sdpads_layout(8),mode,100000000,.01)
            setattr(m.submodules,name,receiver);receivers.append(receiver)
            m.comb += [receiver.sink.valid.eq(valid),receiver.sink.last.eq(1),
                receiver.sink.block_length.eq(length),receiver.source.ready.eq(ready),
                receiver.pads_in.valid.eq(pad_valid),receiver.pads_in.data.i.eq(pad_data),
                receiver.pads_in.cmd.i.eq(1),receiver.pads_out.ready.eq(1),receiver.timeout.storage.eq(timeout)]
        local,reference=receivers
        data=[random.Random(182+speed*7+size+i).randrange(256) for i in range(size)]
        received=[];last=[];cycles=[0]

        def next_ready():return cycles[0]%113<60 or cycles[0]%113>85

        def step(pulse=0,value=255,check=True):
            assert cycles[0]<30000, ('cycle bound',speed,size,last,len(received))
            take=next_ready()
            yield [ready.eq(take),pad_valid.eq(pulse),pad_data.eq(value)]
            yield
            # Outputs are registered/selected independently of ready; sample
            # the word that the next edge will retire with the chosen ready.
            if check and (yield local.source.valid) and (yield ready):
                status=(yield local.source.status)
                if not (yield local.source.drop) and status!=1:
                    received.append((yield local.source.data))
                if (yield local.source.last):last.append(status)
            cycles[0]+=1
            if check:
                for left,right in ((local.source.valid,reference.source.valid),
                                   (local.stop,reference.stop),(local.sink.ready,reference.sink.ready),
                                   (local.pads_out.clk,reference.pads_out.clk)):
                    self.assertEqual((yield left),(yield right),('control',speed,size,cycles[0]))
                if (yield local.source.valid):
                    for field in ('data','status','drop','first','last'):
                        self.assertEqual((yield getattr(local.source,field)),
                                         (yield getattr(reference.source,field)),
                                         (field,speed,size,cycles[0]))

        def bench():
            yield m.cd_sys.rst.eq(1)
            for _ in range(6):yield from step(check=False)
            yield [m.cd_sys.rst.eq(0),valid.eq(1)]
            for _ in range(6):yield from step()
            if not timeout_case:
                for word in pad_words(data,(1,4,8)[speed],corrupt):
                    # Stop the card clock while a completed byte waits for
                    # the consumer; bit order and CRC clocks remain intact.
                    while (yield local.source.valid) and not next_ready():
                        yield from step()
                    yield from step(1,word)
                    for _ in range(5):yield from step()
            for _ in range(1000):
                if last:break
                yield from step()
            self.assertEqual(last,[1 if timeout_case else 5 if corrupt else 0])
            self.assertEqual(received,[] if timeout_case else data)
            yield valid.eq(0)
            for _ in range(12):yield from step()
        class BoundedSimulator(Simulator):
            def _commit_and_comb_propagate(self):
                modified=self.evaluator.commit();iterations=0
                while modified:
                    self.evaluator.execute(self.fragment.comb)
                    modified=self.evaluator.commit();iterations+=1
                    if iterations>100:
                        details=[(sig.backtrace,self.evaluator.signal_values[sig]) for sig in modified]
                        raise AssertionError(('comb loop',cycles[0],details))
        with BoundedSimulator(m,bench(),clocks={'sys':10}) as sim:sim.run()

    def test_pins_crc_stalls_and_timeout(self):
        # Migen's native evaluator settles whole combinational transactions;
        # its generated NBA valid/ready case blocks oscillate in Icarus.
        for speed in (0,1,2):
            for size in (1,8,32):
                for corrupt in (False,True):
                    with self.subTest(speed=speed,size=size,corrupt=corrupt):
                        self.check_case(speed,size,corrupt)
        self.check_case(1,512)
        self.check_case(1,8,timeout_case=True)


if __name__=='__main__':unittest.main()
