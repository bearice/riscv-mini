"""P3a host proof: loopback FIFO, CSR pages, IRQ wire and DMA request levels.

Run: python tests/mega138k_ae350/test_ae350_platform.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts' / 'tests'))

from migen import *
from migen.sim import run_simulation
from litex.gen import LiteXModule
from litex.soc.interconnect.ahb import AHBInterface

from gateware.description import resolve_machine
from gateware.machines.mega138k_ae350.address_map import AddressMap
from gateware.machines.mega138k_ae350.ports import bind_ports
_machine = resolve_machine('mega138k_ae350')
_io = bind_ports(_machine)
amap = AddressMap(_machine)
from gateware.machines.mega138k_ae350.fabric import AE350Fabric
from test_ae350_interconnect import xact


class Cpu(LiteXModule):
    def __init__(self):
        self.ahb_exts = AHBInterface(data_width=32, address_width=32)
        self.apb_addr = Signal(32)
        self.apb_data = Signal(32)
        self.apb_wdata = Signal(32)
        self.apb_strb = Signal(4)
        self.apb_select = Signal()
        self.apb_enable = Signal()
        self.apb_write = Signal()
        self.apb_ready = Signal(reset=1)
        self.apb_slverr = Signal()
        self.dma_req = Signal(8)
        self.dma_ack = Signal(8)
        self.interrupt = Signal(16)


def apb(cpu, write, addr, data=0, strb=0xf):
    # Statements in one yield share a cycle. The registered response is
    # visible to a generator only on the cycle after the access edge.
    yield [cpu.apb_select.eq(1), cpu.apb_enable.eq(0), cpu.apb_write.eq(1 if write else 0),
           cpu.apb_addr.eq(addr), cpu.apb_wdata.eq(data), cpu.apb_strb.eq(strb)]
    yield
    yield cpu.apb_enable.eq(1)
    yield
    yield
    for _ in range(96):
        if (yield cpu.apb_ready):
            break
        yield
    else:
        raise AssertionError('APB ready timeout')
    err = yield cpu.apb_slverr
    value = yield cpu.apb_data
    ready = yield cpu.apb_ready
    yield [cpu.apb_select.eq(0), cpu.apb_enable.eq(0)]
    yield
    return err, value, ready


def main():
    fails = []

    def check(name, cond, detail=''):
        print(('PASS ' if cond else 'FAIL ') + name + ((' ' + str(detail)) if detail != '' else ''))
        if not cond:
            fails.append(name)

    cpu = Cpu()
    diag = Signal(32, reset=0xA3500300)
    # A minimal video endpoint keeps this test focused on the interrupt
    # routing and SYSINFO contract; the real endpoint is covered in RTL tests.
    class VideoEndpoint:
        def __init__(self):
            for name,bits in (('irq',1),('ok',1),('rdata',32),('off',12),
                              ('write',1),('wdata',32),('commit',1),('selected',1)):
                setattr(self,name,Signal(bits))
    video=VideoEndpoint()
    dut = AE350Fabric(cpu, {0: diag, 4: Signal(32, reset=7)}, io=_io, layout=amap, video=video)
    bus = cpu.ahb_exts

    def tb():
        check('address contract', amap.check() == [], amap.check())
        yield video.irq.eq(1)
        yield
        yield
        check('video IRQ EXT_INT routing', (yield cpu.interrupt)==(1<<amap.EXT_INT_VIDEO))
        yield video.irq.eq(0)
        yield
        yield
        check('video IRQ level deassertion', (yield cpu.interrupt)==0)
        err, descriptor, _ = yield from apb(cpu, False, amap.SYSINFO_BASE+0x138)
        check('video IRQ discovery', not err and descriptor==amap.VIDEO_EP[6] and
              (descriptor&255)==amap.EXT_INT_VIDEO and (descriptor>>16)&amap.FLAG_IRQ)

        err, value, ready = yield from apb(cpu, False, amap.SYSINFO_BASE)
        check('sysinfo id', err == 0 and ready == 1 and value == amap.SYSINFO_ID, hex(value))
        for addr, expected in ((amap.BOOT_CONTRACT_ADDR, 1), (amap.BOOT_CLOCK_ADDR, amap.FABRIC_HZ),
                               (amap.BOOT_IMAGE_ABI_ADDR, amap.image_abi())):
            err, value, _ = yield from apb(cpu, False, addr)
            check('stable boot discovery', err == 0 and value == expected, hex(value))
        err, value, _ = yield from apb(cpu, True, amap.SYSINFO_BASE, 0)
        check('sysinfo rejects writes', err == 1, err)
        err, value, _ = yield from apb(cpu, False, amap.SYSINFO_BASE)
        check('sysinfo id unchanged', err == 0 and value == amap.SYSINFO_ID, hex(value))
        err, value, _ = yield from apb(cpu, False, amap.SYSINFO_BASE + 0x08)
        check('HDMI fabric reader capability', err == 0 and
              (value & (amap.CAP_FABRIC_READER | amap.CAP_VIDEO)) ==
              (amap.CAP_FABRIC_READER | amap.CAP_VIDEO), hex(value))
        err, value, _ = yield from apb(cpu, False, amap.SYSINFO_BASE + 0x40)
        check('loopback device entry', err == 0 and value == amap.LOOPBACK_ID, hex(value))
        for base, identity, table, selector_off, selector in (
            (amap.AUDIO_BASE, amap.AUDIO_ID, 0xe0, 0x44, 9),
            (amap.MIC_BASE, amap.MIC_ID, 0x100, 0x48, 10)):
            err, value, _ = yield from apb(cpu, False, base)
            check('audio device identity', err == 0 and value == identity, hex(value))
            err, value, _ = yield from apb(cpu, False, amap.SYSINFO_BASE + table + 28)
            check('controller selector in device table', err == 0 and value == selector, value)
            err, value, _ = yield from apb(cpu, False, base + selector_off)
            check('controller selector in device page', err == 0 and value == selector, value)
        err, _, _ = yield from apb(cpu, True, amap.AUDIO_BASE + 0x0c, 1, strb=1)
        check('partial audio control write rejected', err == 1)
        _, value, _ = yield from apb(cpu, False, amap.AUDIO_BASE + 0x0c)
        check('rejected write has no audio side effect', value == 2, value)
        err, _, _ = yield from apb(cpu, False, amap.SYSINFO_BASE + 0x1000)
        check('unmapped apb errors', err == 1, err)
        err, _, _ = yield from apb(cpu, False, amap.DDR_CSR_BASE + 1)
        check('unaligned apb errors', err == 1, err)
        err, value, _ = yield from apb(cpu, False, amap.DDR_CSR_BASE)
        check('ddr diagnostic still readable', err == 0 and value == 0xA3500300, hex(value))
        err, _, _ = yield from apb(cpu, True, amap.DDR_CSR_BASE, 0)
        check('ddr diagnostic rejects writes', err == 1, err)

        resp, _ = yield from xact(bus, True, amap.LOOPBACK_DATA, 0x11223344, size=2)
        check('push accepted', resp == 0, resp)
        resp, _ = yield from xact(bus, True, amap.LOOPBACK_DATA + 4, 0x55, size=2)
        check('hole errors', resp == 1, resp)
        resp, data = yield from xact(bus, False, amap.LOOPBACK_DATA, size=2)
        check('pop after hole', resp == 0 and data == 0x11223344, hex(data))
        resp, _ = yield from xact(bus, False, amap.LOOPBACK_DATA, size=2)
        check('empty pop errors', resp == 1, resp)
        err, value, _ = yield from apb(cpu, False, amap.DMA_SVC_BASE + 0x10)
        check('underflow sticky', err == 0 and (value & 0x8) != 0, hex(value))
        resp, _ = yield from xact(bus, True, amap.LOOPBACK_DATA, 0x99, size=0)
        check('narrow write errors', resp == 1, resp)
        err, value, _ = yield from apb(cpu, False, amap.DMA_SVC_BASE + 0x20)
        check('narrow write did not push', err == 0 and value == 0, value)

        # Address phase still carries the previous beat's HWDATA (0x0A).
        yield [bus.addr.eq(amap.LOOPBACK_DATA), bus.size.eq(2), bus.write.eq(1),
               bus.wdata.eq(0x0A), bus.sel.eq(0xff), bus.trans.eq(2)]
        yield
        yield [bus.trans.eq(0), bus.wdata.eq(0x44556677)]
        yield
        while not (yield bus.readyout):
            yield
        resp = yield bus.resp
        yield
        check('data-phase write accepted', resp == 0, resp)
        resp, data = yield from xact(bus, False, amap.LOOPBACK_DATA, size=2)
        check('data-phase pop', resp == 0 and data == 0x44556677, hex(data))

        resp, _ = yield from xact(bus, True, amap.LOOPBACK_DATA, 1, size=2, sel=0)
        check('deselected port does not push', resp == 0, resp)
        err, value, _ = yield from apb(cpu, False, amap.DMA_SVC_BASE + 0x20)
        check('level still empty', value == 0, value)

        for i in range(amap.LOOPBACK_DEPTH):
            resp, _ = yield from xact(bus, True, amap.LOOPBACK_DATA, 0xA000 + i, size=2)
            if resp != 0:
                check('fill', False, i)
                break
        else:
            check('fill', True)
        resp, _ = yield from xact(bus, True, amap.LOOPBACK_DATA, 0xEEEE, size=2)
        check('full push errors', resp == 1, resp)
        err, value, _ = yield from apb(cpu, False, amap.DMA_SVC_BASE + 0x10)
        check('overflow sticky and full', err == 0 and (value & 0x6) == 0x6, hex(value))
        for i in range(amap.LOOPBACK_DEPTH):
            resp, data = yield from xact(bus, False, amap.LOOPBACK_DATA, size=2)
            if resp != 0 or data != 0xA000 + i:
                check('drain order', False, (i, resp, hex(data)))
                break
        else:
            check('drain order', True)

        err, _, _ = yield from apb(cpu, True, amap.IRQ_BASE + 0x18, 1)
        check('irq enable', err == 0, err)
        resp, _ = yield from xact(bus, True, amap.LOOPBACK_DATA, 0x44556677, size=2)
        check('irq push', resp == 0, resp)
        err, value, _ = yield from apb(cpu, False, amap.IRQ_BASE + 0x14)
        check('irq pending', err == 0 and value == 1 and (yield cpu.interrupt) == 1, (value, hex((yield cpu.interrupt))))
        err, _, _ = yield from apb(cpu, True, amap.IRQ_BASE + 0x14, 1)
        check('irq clear', err == 0 and (yield cpu.interrupt) == 0, (yield cpu.interrupt))
        resp, data = yield from xact(bus, False, amap.LOOPBACK_DATA, size=2)
        check('data remains after irq clear', resp == 0 and data == 0x44556677, hex(data))

        err, _, _ = yield from apb(cpu, True, amap.DMA_SVC_BASE + 0x0c, 0x2)
        check('request enable', err == 0, err)
        req = yield cpu.dma_req
        check('tx request while empty', (req & 3) == 1, hex(req))
        yield from xact(bus, True, amap.LOOPBACK_DATA, 0x10, size=2)
        req = yield cpu.dma_req
        check('rx request while occupied', (req & 3) == 3, hex(req))
        yield cpu.dma_ack.eq(1)
        yield
        req = yield cpu.dma_req
        check('tx request falls while ack is high', (req & 1) == 0, hex(req))
        yield cpu.dma_ack.eq(0)
        yield
        yield
        yield
        yield
        req = yield cpu.dma_req
        check('tx request rises after ack falls', (req & 1) == 1, hex(req))
        err, value, _ = yield from apb(cpu, False, amap.DMA_SVC_BASE + 0x2c)
        check('ack rising edge counted', err == 0 and (value & 0xffff) == 1, hex(value))
        for i in range(amap.LOOPBACK_DEPTH - 1):
            resp, _ = yield from xact(bus, True, amap.LOOPBACK_DATA, 0xB000 + i, size=2)
            if resp != 0:
                check('fill to the request limit', False, (i, resp))
                break
        else:
            req = yield cpu.dma_req
            check('tx request stays low when full', (req & 1) == 0, hex(req))
        resp, _ = yield from xact(bus, False, amap.LOOPBACK_DATA, size=2)
        req = yield cpu.dma_req
        check('tx request returns after one pop', resp == 0 and (req & 1) == 1, (resp, hex(req)))
        err, _, _ = yield from apb(cpu, True, amap.DMA_SVC_BASE + 0x0c, 0x1)
        check('soft reset accepted', err == 0, err)
        err, value, _ = yield from apb(cpu, False, amap.DMA_SVC_BASE + 0x20)
        check('soft reset empties the fifo', err == 0 and value == 0, value)
        err, value, _ = yield from apb(cpu, False, amap.IRQ_BASE + 0x14)
        check('soft reset clears irq', err == 0 and value == 0, value)
        err, value, _ = yield from apb(cpu, False, amap.DMA_SVC_BASE + 0x10)
        check('soft reset clears sticky errors', err == 0 and value == 1, hex(value))

    fragment = dut.get_fragment()
    # Pinned Migen needs an unused read signal on write-only FIFO RAM ports.
    from migen.fhdl.specials import Memory
    for memory in fragment.specials:
        if isinstance(memory, Memory):
            for port in memory.ports:
                if port.dat_r is None:
                    port.dat_r = Signal(memory.width)
    run_simulation(fragment, tb(), clocks={'sys': 10})
    print('RESULT', 'PASS' if not fails else 'FAIL: ' + ','.join(fails))
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main())
