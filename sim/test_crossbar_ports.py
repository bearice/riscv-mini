"""Independent port wiring, boot isolation and command/data backpressure.

The backend is a native-port stub, not a physical DDR or bank-machine model.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import Signal
from migen.sim import run_simulation
from litedram.common import LiteDRAMNativePort
from gateware.memory import CrossbarPorts


class PortStub:
    def __init__(self):self.ports=[]
    def get_port(self,mode):
        port=LiteDRAMNativePort(mode,23,128)
        self.ports.append(port)
        return port


def main():
    crossbar=PortStub();ready=Signal()
    dut=CrossbarPorts(crossbar,enabled=ready)
    cpu,video=dut.cpu,dut.video
    c,v=crossbar.ports
    def check():
        yield cpu.cmd.valid.eq(1);yield video.cmd.valid.eq(1)
        yield cpu.cmd.addr.eq(0x1234);yield video.cmd.addr.eq(0x5678)
        yield c.cmd.ready.eq(1);yield v.cmd.ready.eq(1);yield
        assert not (yield c.cmd.valid) and not (yield v.cmd.valid)
        assert not (yield cpu.cmd.ready) and not (yield video.cmd.ready)
        yield ready.eq(1);yield
        assert (yield c.cmd.valid) and (yield v.cmd.valid)
        assert (yield c.cmd.addr)==0x1234 and (yield v.cmd.addr)==0x5678
        assert (yield cpu.cmd.ready) and (yield video.cmd.ready)
        yield c.cmd.ready.eq(0);yield
        assert not (yield cpu.cmd.ready) and (yield video.cmd.ready)
        yield c.rdata.valid.eq(1);yield v.rdata.valid.eq(1)
        yield c.rdata.data.eq(0x123456789abcdef);yield v.rdata.data.eq(0xfedcba987654321)
        yield cpu.rdata.ready.eq(1);yield video.rdata.ready.eq(0);yield
        assert (yield cpu.rdata.valid) and (yield video.rdata.valid)
        assert (yield cpu.rdata.data)==0x123456789abcdef
        assert (yield video.rdata.data)==0xfedcba987654321
        assert (yield c.rdata.ready) and not (yield v.rdata.ready)
        yield cpu.wdata.valid.eq(1);yield cpu.wdata.data.eq(0x11223344)
        yield cpu.wdata.we.eq(0x0005);yield c.wdata.ready.eq(0);yield
        assert (yield c.wdata.valid) and not (yield cpu.wdata.ready)
        assert (yield c.wdata.data)==0x11223344 and (yield c.wdata.we)==5
        yield c.wdata.ready.eq(1);yield
        assert (yield cpu.wdata.ready)
        print('Crossbar port wiring PASS: boot gate, independent commands/responses, backpressure and byte mask')
    run_simulation(dut,check())


if __name__=='__main__':main()
