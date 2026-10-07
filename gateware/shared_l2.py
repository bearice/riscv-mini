"""Single shared cache for a 32-bit Wishbone master and 128-bit streaming clients.

Streaming reads do not allocate, but hit dirty CPU lines. All accepted writes
complete in cache or DDR before acknowledging their producer. One transaction
owns the synchronous RAM and DDR port through completion, including cancellation.
"""
from gateware.memory_map import BIOS_BASE, BOOT_RAM_BASE, RAM_BASE, RAM_END
from migen import Signal, Memory, Cat, Constant, Replicate, Mux, If, Case, FSM, NextState, NextValue
from litex.gen import LiteXModule
from litex.soc.interconnect.csr import CSR, CSRStorage, CSRStatus
from litedram.common import LiteDRAMNativePort


class SharedL2(LiteXModule):
    def __init__(self, master, backend, size=4096, writeback=True, enabled=1, video=None, dma=None, boot_ram=False, maintenance_ready=1):
        if isinstance(enabled,int):enabled=Constant(enabled,1)
        if size < 256 or size & (size-1):raise ValueError('Invalid shared L2 size')
        if boot_ram and (size < 4096 or not writeback):raise ValueError('Boot RAM requires >=4 KiB writeback L2')
        self.video=video=video or LiteDRAMNativePort('read',backend.address_width,128)
        self.dma=dma=dma or LiteDRAMNativePort('both',backend.address_width,128)
        self._enable=CSRStorage(1,reset=1,name='enable')
        self._stats=CSRStatus(32,name='stats')
        self._flush=CSR(name='flush')
        self._invalidate=CSR(name='invalidate')
        self._busy=CSRStatus(name='busy')
        bits=(size//16-1).bit_length();aw=backend.address_width;tb=aw-bits
        data=Memory(128,size//16,name='shared_l2_data')
        tags=Memory(tb+2,size//16,name='shared_l2_tags')
        dp=data.get_port(write_capable=True);tp=tags.get_port(write_capable=True)
        self.specials += data,tags,dp,tp
        address=Signal(aw);payload=Signal(128);mask=Signal(16);write=Signal()
        allocate=Signal();owner=Signal(2);lane=Signal(2)
        # Only one owned transaction exists: read responses can reuse the
        # write payload register after the request has been classified.
        response=payload;maintenance_evict=Signal();cmd_addr=Signal(aw);cmd_we=Signal();cmd_last=Signal();cmd_valid=Signal()
        abandoned=Signal();cursor=Signal(bits);cleaning=Signal(reset=1)
        invalidate=Signal();request=Signal();invalidate_request=Signal();active=Signal(reset=1)
        hits=Signal(16);misses=Signal(16);last=Signal(2,reset=2);choice=Signal(2)
        pending=[master.cyc & master.stb,video.cmd.valid,dma.cmd.valid]
        self.maintenance_pending=Signal()
        self.comb += self.maintenance_pending.eq(request | cleaning | (active != self._enable.storage))
        # Round robin at complete transaction boundaries, so a streaming video
        # reader cannot starve the CPU and a CPU burst cannot starve scanout.
        self.comb += Case(last,{i:
            If(pending[(i+1)%3],choice.eq((i+1)%3)).Elif(pending[(i+2)%3],choice.eq((i+2)%3)).Else(choice.eq(i))
            for i in range(3)})
        self.sync += [If(self._flush.re | self._invalidate.re,request.eq(1)),
                      If(self._invalidate.re,invalidate_request.eq(1)),
                      If((owner==0) & ~master.cyc,abandoned.eq(1))]
        self.fsm=fsm=FSM(reset_state='RESET')
        hit=tp.dat_r[tb] & (tp.dat_r[:tb]==address[bits:])
        dirty=tp.dat_r[tb+1] if writeback else 0
        merged=Cat(*[Mux(mask[i],payload[i*8:(i+1)*8],dp.dat_r[i*8:(i+1)*8]) for i in range(16)])
        self.comb += [dp.adr.eq(Mux(cleaning,cursor,address[:bits])),tp.adr.eq(dp.adr),
            dp.dat_w.eq(merged),tp.dat_w.eq(Cat(address[bits:],1,0)),
            self._stats.status.eq(Cat(hits,misses)),
            self._busy.status.eq(cleaning | request | (active != self._enable.storage)),
            backend.cmd.valid.eq(cmd_valid),backend.cmd.addr.eq(cmd_addr),backend.cmd.we.eq(cmd_we),
            backend.cmd.last.eq(cmd_last),backend.wdata.data.eq(payload),backend.wdata.we.eq(mask),
            Case(lane,{i:master.dat_r.eq(response[32*i:32*(i+1)]) for i in range(4)}),
            video.rdata.data.eq(response),dma.rdata.data.eq(response)]
        # cmd.ready only clears this flop; no FSM next-state logic depends on it.
        # Data phases wait for ~cmd_valid, so data never precedes its command.
        self.sync += If(backend.cmd.valid & backend.cmd.ready,cmd_valid.eq(0))
        # Before DDR is ready, pin 407ff000..407fffff in the existing data RAM.
        # Dirty tags preserve the live stack across handover: its first eviction
        # writes the boot data to DDR, without a refill or stack relocation.
        boot_tag=(0x7ff000//16)>>bits
        boot_index=cursor >= size//16-256
        fsm.act('RESET',tp.dat_w.eq(Cat(Constant(boot_tag,tb),1,1) if boot_ram else 0),
            tp.we.eq(1),
            *([If(~boot_index,tp.dat_w.eq(0)),dp.we.eq(1),dp.dat_w.eq(0)] if boot_ram else []),
            If(cursor==size//16-1,NextValue(cursor,0),NextValue(cleaning,0),NextState('IDLE')).Else(NextValue(cursor,cursor+1)))
        cpu_capture=[NextValue(owner,0),NextValue(address,master.adr[2:]-(RAM_BASE>>4)),
            NextValue(payload,Replicate(master.dat_w,4)),
            # Concatenation preserves all four shift bits in generated Verilog.
            # A 2-bit lane * 3-bit constant has only a 3-bit self-determined
            # result as a shift operand, aliasing lanes 2/3 onto lanes 0/1.
            NextValue(mask,master.sel << Cat(0,0,master.adr[:2])),NextValue(write,master.we),
            NextValue(lane,master.adr[:2]),NextValue(abandoned,0),
            NextValue(allocate,active & (master.adr < (RAM_END>>2))),NextState('RAM')]
        fsm.act('IDLE',
            If((request | (active != self._enable.storage)) & (enabled if boot_ram else 1) & maintenance_ready,
                NextValue(cleaning,1),NextValue(cursor,0),
                NextValue(invalidate,invalidate_request | ~self._enable.storage),
                NextValue(request,0),NextValue(invalidate_request,0),NextState('SCAN_RAM')
            ).Elif((~enabled if boot_ram else 0),
                If(pending[0] & (master.adr >= (BOOT_RAM_BASE>>2)) & (master.adr < (BIOS_BASE>>2)),
                    *cpu_capture)
            ).Elif(enabled,
                Case(choice,{
                    0:If(pending[0],*cpu_capture),
                    1:If(video.cmd.valid,video.cmd.ready.eq(1),NextValue(owner,1),
                        NextValue(address,video.cmd.addr),NextValue(write,0),NextValue(allocate,0),
                        NextState('RAM')),
                    2:If(dma.cmd.valid,dma.cmd.ready.eq(1),NextValue(owner,2),
                        NextValue(address,dma.cmd.addr),NextValue(write,dma.cmd.we),NextValue(allocate,0),
                        If(dma.cmd.we,NextState('DMA_DATA')).Else(NextState('RAM')))})))
        # Keep producer ownership until cache/DDR commit. Capture held write
        # data without ready; ready is returned only in COMPLETE.
        fsm.act('DMA_DATA',If(dma.wdata.valid,NextValue(payload,dma.wdata.data),
            NextValue(mask,dma.wdata.we),NextState('RAM')))
        fsm.act('RAM',NextState('LOOKUP'))
        start_evict=lambda tgt:[NextValue(cmd_addr,tgt),NextValue(cmd_we,1),NextValue(cmd_last,0),NextValue(cmd_valid,1),NextState('EVICT_DATA')]
        start_refill=[NextValue(cmd_addr,address),NextValue(cmd_we,0),NextValue(cmd_last,1),NextValue(cmd_valid,1),NextState('REFILL_DATA')]
        start_cmd=[NextValue(cmd_addr,address),NextValue(cmd_we,write),NextValue(cmd_last,~write),NextValue(cmd_valid,1),If(write,NextState('WRITE')).Else(NextState('READ'))]
        fsm.act('LOOKUP',
            If(write & (not writeback),
                tp.we.eq(1),tp.dat_w.eq(0),*start_cmd
            ).Elif(hit & active,
                NextValue(hits,hits+1),
                If(write,
                    If(writeback,NextState('STORE')).Else(tp.we.eq(1),tp.dat_w.eq(0),*start_cmd)
                ).Else(NextValue(response,dp.dat_r),NextState('COMPLETE'))
            ).Else(
                NextValue(misses,misses+1),
                If(allocate,
                    # CPU writes cover at most four bytes; streaming writes do not
                    # allocate. There is no allocating full-line write shortcut.
                    If(tp.dat_r[tb] & dirty,NextValue(maintenance_evict,0),*start_evict(Cat(address[:bits],tp.dat_r[:tb]))).Else(*start_refill)
                ).Else(*start_cmd)))
        fsm.act('STORE',dp.we.eq(1),tp.we.eq(1),tp.dat_w.eq(Cat(address[bits:],1,1)),NextState('COMPLETE'))
        fsm.act('EVICT_DATA',backend.wdata.valid.eq(~cmd_valid),backend.wdata.data.eq(dp.dat_r),backend.wdata.we.eq(0xffff),
            If(~cmd_valid & backend.wdata.ready,
                If(maintenance_evict,NextState('SCAN_CLEAR')).Else(*start_refill)))
        fsm.act('REFILL_DATA',backend.rdata.ready.eq(1),
            If(backend.rdata.valid,dp.we.eq(1),dp.dat_w.eq(backend.rdata.data),tp.we.eq(1),
                If(write,NextState('REFILL_WAIT')).Else(NextValue(response,backend.rdata.data),NextState('COMPLETE'))))
        fsm.act('REFILL_WAIT',NextState('STORE'))
        fsm.act('WRITE',backend.wdata.valid.eq(~cmd_valid),If(~cmd_valid & backend.wdata.ready,NextState('COMPLETE')))
        fsm.act('READ',backend.rdata.ready.eq(1),
            If(backend.rdata.valid,NextValue(response,backend.rdata.data),NextState('COMPLETE')))
        fsm.act('COMPLETE',
            If(owner==0,master.ack.eq(master.cyc & ~abandoned),NextValue(last,0),NextState('IDLE'))
            .Elif(owner==1,video.rdata.valid.eq(1),If(video.rdata.ready,NextValue(last,1),NextState('IDLE')))
            .Elif(write,dma.wdata.ready.eq(1),If(dma.wdata.valid,NextValue(last,2),NextState('IDLE')))
            .Else(dma.rdata.valid.eq(1),If(dma.rdata.ready,NextValue(last,2),NextState('IDLE'))))
        fsm.act('SCAN_RAM',NextState('SCAN_CHECK'))
        fsm.act('SCAN_CHECK',
            If(tp.dat_r[tb] & dirty,NextValue(maintenance_evict,1),*start_evict(Cat(cursor,tp.dat_r[:tb]))).Else(NextState('SCAN_CLEAR')))

        fsm.act('SCAN_CLEAR',tp.we.eq(1),tp.dat_w.eq(Mux(invalidate,0,Cat(tp.dat_r[:tb+1],0))),
            If(cursor==size//16-1,NextValue(cleaning,0),NextValue(active,self._enable.storage),NextState('IDLE'))
            .Else(NextValue(cursor,cursor+1),NextState('SCAN_RAM')))
