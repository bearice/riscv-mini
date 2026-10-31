"""Autonomous DDR descriptor rings; CPU never transfers packet bytes over MMIO.

Each descriptor occupies a complete 32-byte cache line: buffer, capacity,
length, flags (OWN31/DONE30/ERROR29), cookie, and three reserved words.
TX completion releases the buffer after the MAC has copied the complete
frame. RX completion follows the acknowledgement of every DDR payload write.
Stopping drains accepted transactions; no read is cancelled or reassigned.
"""
from migen import *
from litex.gen import LiteXModule
from litex.soc.interconnect import stream
from liteeth.common import eth_phy_description
from gateware.machines.mega138k_ae350.ddr3_ahb import DDR3UI
from gateware.machines.mega138k_ae350.sd_cdc import crossing


class EthernetDDRPort(LiteXModule):
    def __init__(self):
        self.ui=ui=DDR3UI()
        self.pending=Signal()
        self.requests=q=crossing([('addr',32),('write',1),('data',256),('mask',32)],'sys','ddr')
        self.responses=r=crossing([('data',256)],'ddr','sys')
        f=FSM(reset_state='IDLE');self.backend=ClockDomainsRenamer('ddr')(f)
        addr,data,mask=Signal(32),Signal(256),Signal(32)
        write=Signal();result=Signal(256)
        self.comb += [ui.addr.eq(addr[2:31]),ui.cmd.eq(Mux(write,0,1)),ui.burst.eq(0),
            ui.wr_data.eq(data),ui.wr_data_mask.eq(mask),ui.wr_data_end.eq(ui.wr_data_en),
            r.sink.data.eq(result)]
        f.act('IDLE',q.source.ready.eq(1),If(q.source.valid,
            NextValue(addr,q.source.addr),NextValue(data,q.source.data),
            NextValue(mask,q.source.mask),NextValue(write,q.source.write),NextState('SEND')))
        f.act('SEND',self.pending.eq(1),ui.cmd_en.eq(~write|ui.wr_data_rdy),
            ui.wr_data_en.eq(write & ui.cmd_ready & ui.wr_data_rdy),
            If(ui.cmd_ready & (~write|ui.wr_data_rdy),
                If(write,NextValue(result,0),NextState('RESP')).Else(NextState('READ'))))
        f.act('READ',If(ui.rd_data_valid,NextValue(result,ui.rd_data),NextState('RESP')))
        f.act('RESP',r.sink.valid.eq(1),If(r.sink.ready,NextState('IDLE')))


class EthernetDMA(LiteXModule):
    mtu=1530
    def __init__(self, ram_bytes=1<<30, min_address=0x100000, default_rx_taps=200):
        self.off,self.wdata,self.rdata=Signal(12),Signal(32),Signal(32)
        self.selected,self.write,self.commit,self.ok=[Signal() for _ in range(4)]
        self.sink=tx=stream.Endpoint(eth_phy_description(32))
        self.source=rx=stream.Endpoint(eth_phy_description(32))
        self.memory=EthernetDDRPort()
        q,r=self.memory.requests.sink,self.memory.responses.source
        self.phy_status=Signal(2);self.rx_clock_events=Signal(32)
        self.phy_rx_frames,self.phy_tx_frames=Signal(32),Signal(32)
        self.phy_rx_er_frames=Signal(32)
        self.mac_crc_errors,self.mac_preamble_errors,self.mac_rx_drops=[Signal(32) for _ in range(3)]
        self.tx_frames,self.rx_frames,self.rx_drops,self.rx_errors=[Signal(32) for _ in range(4)]
        self.rx_er_frames,self.rx_fcs_errors=Signal(32),Signal(32)
        self.rx_word_rx_er=Signal(4);self.rx_fcs_rx_er=Signal(4);self.rx_frame_fcs_error=Signal()
        self.rx_delay_taps=[Signal(8,reset=default_rx_taps) for _ in range(5)]
        self.rx_delay_load=Signal()
        self.irq=Signal()
        enabled=Signal();txbase,rxbase=Signal(32),Signal(32);mask=Signal(8,reset=3)
        txprod,txcons,rxprod,rxcons=[Signal(32) for _ in range(4)]
        irq_pending,irq_enable,event=Signal(3),Signal(3),Signal(3)
        fault=Signal(4)
        pending=Signal();length,count=Signal(11),Signal(11)
        drop,bad,first=Signal(),Signal(),Signal(reset=1)
        f=self.fsm=FSM(reset_state='IDLE')
        busy=Signal();self.comb += busy.eq(~f.ongoing('IDLE'))
        self.busy=Signal();self.comb += self.busy.eq(busy|pending|(~first & ~drop))
        discard=Signal()
        bytes_valid=Signal(3);frame_end=Signal(12);bad_be=Signal()
        self.comb += [bytes_valid.eq(sum(rx.be[i] for i in range(4))),
            frame_end.eq(count+bytes_valid),bad_be.eq(Mux(rx.last,
                (rx.be!=1)&(rx.be!=3)&(rx.be!=7)&(rx.be!=15),rx.be!=15))]
        word_index=Signal(3);read_word=Signal(32);read_address=Signal(9)
        self.comb += [rx.ready.eq(1),discard.eq(Mux(first,pending|~enabled|(fault!=0),drop))]
        # Separate byte banks avoid Gowin mapping a wide, byte-enabled
        # array into thousands of registers. Four synchronous byte reads
        # yield one word; eight words form a native 256-bit DDR beat.
        readers=[]
        for lane in range(4):
            mem=Memory(8,512,name='eth_rx_bank%d'%lane);self.specials += mem
            writer=mem.get_port(write_capable=True);reader=mem.get_port()
            self.specials += writer,reader;readers.append(reader.dat_r)
            self.comb += [writer.adr.eq(count[2:]),writer.dat_w.eq(rx.data[8*lane:8*(lane+1)]),
                writer.we.eq(rx.valid & ~discard & (count<self.mtu) & rx.be[lane]),
                reader.adr.eq(read_address)]
        self.comb += read_word.eq(Cat(*readers))
        # Bounded first-bad-frame capture (64 words = 256 bytes + per-word 4-bit RX_ER mask).
        # Captures every incoming frame until locked on the first bad frame (RX_ER or FCS error),
        # while the normal DMA path still drops/rejects bad frames (`pending` remains 0).
        diag_arm=Signal(reset=1);diag_locked=Signal()
        diag_len=Signal(11);diag_flags=Signal(4)
        diag_first_err_byte=Signal(11);diag_err_seen=Signal()
        diag_fcs_rx_er_mask=Signal(4);diag_raddr=Signal(6)
        diag_data_mem=Memory(32,64,name='eth_rx_diag_data')
        diag_err_mem=Memory(4,64,name='eth_rx_diag_err')
        self.specials += diag_data_mem,diag_err_mem
        diag_dw=diag_data_mem.get_port(write_capable=True);diag_dr=diag_data_mem.get_port()
        diag_ew=diag_err_mem.get_port(write_capable=True);diag_er=diag_err_mem.get_port()
        self.specials += diag_dw,diag_dr,diag_ew,diag_er
        diag_capture_we=Signal()
        self.comb += [
            diag_capture_we.eq(rx.valid & diag_arm & ~diag_locked & (count[2:]<64)),
            diag_dw.adr.eq(count[2:8]),diag_dw.dat_w.eq(rx.data),diag_dw.we.eq(diag_capture_we),
            diag_ew.adr.eq(count[2:8]),diag_ew.dat_w.eq(self.rx_word_rx_er),diag_ew.we.eq(diag_capture_we),
            diag_dr.adr.eq(diag_raddr),diag_er.adr.eq(diag_raddr),
        ]
        word_first_err_lane=Signal(2)
        self.comb += word_first_err_lane.eq(
            Mux(self.rx_word_rx_er[0],0,Mux(self.rx_word_rx_er[1],1,Mux(self.rx_word_rx_er[2],2,3))))
        frame_is_bad=Signal()
        self.comb += frame_is_bad.eq((frame_end>self.mtu)|(rx.error!=0)|bad_be|(~first & bad))
        release=Signal()
        self.sync += [If(release,pending.eq(0)),If(rx.valid,
            If(first,
                drop.eq(pending|~enabled|(fault!=0)),
                bad.eq((rx.error!=0)|bad_be),
                If(diag_arm & ~diag_locked,
                    If(self.rx_word_rx_er!=0,
                        diag_err_seen.eq(1),
                        diag_first_err_byte.eq(word_first_err_lane),
                    ).Else(
                        diag_err_seen.eq(0),
                        diag_first_err_byte.eq(0x7ff),
                    )
                )
            ).Else(
                bad.eq(bad|(rx.error!=0)|bad_be),
                If(diag_arm & ~diag_locked & ~diag_err_seen & (self.rx_word_rx_er!=0),
                    diag_err_seen.eq(1),
                    diag_first_err_byte.eq(Cat(word_first_err_lane,count[2:11])),
                )
            ),
            first.eq(0),If(count<2044,count.eq(count+bytes_valid)).Else(count.eq(2047)),
            If(rx.last,first.eq(1),count.eq(0),bad.eq(0),drop.eq(0),
                If(discard,self.rx_drops.eq(self.rx_drops+1))
                .Elif(frame_is_bad,
                    self.rx_errors.eq(self.rx_errors+1),
                    If(self.rx_frame_fcs_error,self.rx_fcs_errors.eq(self.rx_fcs_errors+1)),
                    If((self.rx_fcs_rx_er!=0)|(~first & bad)|(self.rx_word_rx_er!=0),
                        self.rx_er_frames.eq(self.rx_er_frames+1)),
                    If(diag_arm & ~diag_locked,
                        diag_locked.eq(1),
                        diag_len.eq(frame_end),
                        diag_fcs_rx_er_mask.eq(self.rx_fcs_rx_er),
                        diag_flags.eq(Cat(
                            (self.rx_fcs_rx_er!=0)|(~first & bad)|(self.rx_word_rx_er!=0),
                            self.rx_frame_fcs_error,
                            bad_be,
                            frame_end>self.mtu,
                        )),
                    )
                ).Else(pending.eq(1),length.eq(frame_end))))]
        reset=Signal();clear=Signal(3)
        self.comb += [self.irq.eq((irq_pending & irq_enable)!=0),
            reset.eq(self.selected & self.commit & self.write & (self.off==0x80) & self.wdata[1] & ~self.busy),
            clear.eq(Mux(self.selected & self.commit & self.write & (self.off==0xa4),self.wdata[:3],0))]
        self.sync += irq_pending.eq((irq_pending & ~clear)|event)
        ring_end_tx,ring_end_rx=Signal(33),Signal(33)
        self.comb += [ring_end_tx.eq(txbase+((mask+1)<<5)),ring_end_rx.eq(rxbase+((mask+1)<<5))]
        config_ok=(mask>=1)&((mask&(mask+1))==0)&(txbase>=min_address)&(rxbase>=min_address)&\
            (txbase[:5]==0)&(rxbase[:5]==0)&(ring_end_tx<=ram_bytes)&(ring_end_rx<=ram_bytes)
        read_offsets=(0x10,0x14,0x18,0x1c,0x38,0x3c,0x40,0x44,0x48,0x4c,0x50,0x54,0x58,0x5c,
                      0x60,0x64,0x68,0x6c,0x70,0x74,0x78,0x7c,
                      0x80,0x84,0x88,0x8c,0x90,0x94,0x98,0x9c,0xa0,0xa4,0xa8,0xac)
        readable=[self.off==v for v in read_offsets]
        from functools import reduce
        from operator import or_, and_
        config_write=((self.off==0x88)|(self.off==0x8c)|(self.off==0x90)) & ~enabled & ~self.busy
        # Dynamic IODELAY tap updates require DMA stopped & drained (~enabled & ~self.busy)
        # and each tap within 0..240 (0..3000 ps; allowed range enforced by hardware).
        delay_ctrl_write=(self.off==0x68) & ~enabled & ~self.busy & (self.wdata[8:31]==0) & (self.wdata[:8]<=240)
        delay_data_write=(self.off==0x6c) & ~enabled & ~self.busy & reduce(and_,[self.wdata[8*i:8*(i+1)]<=240 for i in range(4)])
        diag_ctrl_write=(self.off==0x70) & (self.wdata[2:]==0)
        diag_addr_write=(self.off==0x78) & (self.wdata[6:]==0)
        txused,txadvance,rxused,rxadvance,rxleft=[Signal(32) for _ in range(5)]
        self.comb += [txused.eq(self.wdata-txcons),txadvance.eq(self.wdata-txprod),
            rxused.eq(rxprod-rxcons),rxadvance.eq(self.wdata-rxcons),rxleft.eq(rxprod-self.wdata)]
        prod_write=(self.off==0x94)&(txused<=(mask+1))&(txadvance<=(mask+1))
        cons_write=(self.off==0xa0)&(rxleft<=(mask+1))&(rxadvance<=(mask+1))
        ctrl_write=(self.off==0x80)&(self.wdata[2:]==0)&Mux(self.wdata[1],~self.busy,
            ~self.wdata[0]|(config_ok & (fault==0)))
        writable=(config_write|prod_write|cons_write|ctrl_write|delay_ctrl_write|delay_data_write|
                  diag_ctrl_write|diag_addr_write|(self.off==0xa4)|(self.off==0xa8))
        ok_q=Signal();rdata_q=Signal(32);raw_rdata=Signal(32)
        self.comb += [self.ok.eq(self.selected & ok_q),self.rdata.eq(rdata_q),
            raw_rdata.eq(0),Case(self.off,{
                0x10:raw_rdata.eq(0xae350e03),0x14:raw_rdata.eq(self.mtu),
                0x18:raw_rdata.eq(self.phy_status),0x1c:raw_rdata.eq(self.rx_clock_events),
                0x38:raw_rdata.eq(self.tx_frames),0x3c:raw_rdata.eq(self.rx_frames),
                0x40:raw_rdata.eq(self.rx_drops),0x44:raw_rdata.eq(self.rx_errors),
                0x48:raw_rdata.eq(self.rx_fcs_errors),
                0x4c:raw_rdata.eq(self.mac_crc_errors),0x50:raw_rdata.eq(self.mac_preamble_errors),
                0x54:raw_rdata.eq(self.phy_rx_frames),0x58:raw_rdata.eq(self.phy_tx_frames),
                0x5c:raw_rdata.eq(self.mac_rx_drops),
                0x60:raw_rdata.eq(self.phy_rx_er_frames),0x64:raw_rdata.eq(self.rx_er_frames),
                0x68:raw_rdata.eq(self.rx_delay_taps[0]|(Constant(default_rx_taps,8)<<16)),
                0x6c:raw_rdata.eq(Cat(*self.rx_delay_taps[1:5])),
                0x70:raw_rdata.eq(diag_arm|(diag_locked<<1)|(diag_flags<<8)|(diag_len<<16)),
                0x74:raw_rdata.eq(diag_first_err_byte|(diag_fcs_rx_er_mask<<16)|(diag_er.dat_r<<24)),
                0x78:raw_rdata.eq(diag_raddr),
                0x7c:raw_rdata.eq(diag_dr.dat_r),
                0x80:raw_rdata.eq(enabled),
                0x84:raw_rdata.eq(self.busy|(pending<<1)|((fault!=0)<<8)),
                0x88:raw_rdata.eq(txbase),0x8c:raw_rdata.eq(rxbase),0x90:raw_rdata.eq(mask),
                0x94:raw_rdata.eq(txprod),0x98:raw_rdata.eq(txcons),0x9c:raw_rdata.eq(rxprod),
                0xa0:raw_rdata.eq(rxcons),0xa4:raw_rdata.eq(irq_pending),
                0xa8:raw_rdata.eq(irq_enable),0xac:raw_rdata.eq(fault)})]
        self.sync += [
            ok_q.eq(self.selected & Mux(self.write,writable,reduce(or_,readable))),
            rdata_q.eq(Mux(self.selected & ~self.write,raw_rdata,0)),
            self.rx_delay_load.eq(0),
            If(self.selected & self.commit & self.ok & self.write,Case(self.off,{
                0x68:[
                    self.rx_delay_taps[0].eq(self.wdata[:8]),
                    If(self.wdata[31],*[self.rx_delay_taps[i].eq(self.wdata[:8]) for i in range(1,5)]),
                    self.rx_delay_load.eq(1),
                ],
                0x6c:[
                    self.rx_delay_taps[1].eq(self.wdata[0:8]),
                    self.rx_delay_taps[2].eq(self.wdata[8:16]),
                    self.rx_delay_taps[3].eq(self.wdata[16:24]),
                    self.rx_delay_taps[4].eq(self.wdata[24:32]),
                    self.rx_delay_load.eq(1),
                ],
                0x70:[
                    diag_arm.eq(self.wdata[0]),
                    If(self.wdata[1],
                        diag_locked.eq(0),diag_len.eq(0),diag_flags.eq(0),
                        diag_first_err_byte.eq(0x7ff),diag_err_seen.eq(0),diag_fcs_rx_er_mask.eq(0),
                    ),
                ],
                0x78:diag_raddr.eq(self.wdata[:6]),
                0x80:enabled.eq(self.wdata[0] & ~self.wdata[1]),0x88:txbase.eq(self.wdata),
                0x8c:rxbase.eq(self.wdata),0x90:mask.eq(self.wdata),0x94:txprod.eq(self.wdata),
                0xa0:rxcons.eq(self.wdata),0xa8:irq_enable.eq(self.wdata)}))
        ]
        self.sync += If(reset,txprod.eq(0),txcons.eq(0),rxprod.eq(0),rxcons.eq(0),fault.eq(0),
                        first.eq(1),count.eq(0),drop.eq(0),bad.eq(0))
        desc,chunk=Signal(256),Signal(256)
        descaddr,address=Signal(32),Signal(32)
        remaining,position=Signal(11),Signal(6)
        is_rx,last_rx,error=Signal(),Signal(),Signal()
        buf,capacity,declen,flags=desc[:32],desc[32:64],desc[64:96],desc[96:128]
        end=Signal(33);self.comb += end.eq(buf+capacity)
        valid_buffer=(buf>=min_address)&(buf[:5]==0)&(capacity>=32)&(capacity<=1536)&(capacity[:5]==0)&(end<=ram_bytes)
        available_rx=rxused<(mask+1)
        self.comb += [q.addr.eq(address),q.write.eq(0),q.data.eq(chunk),q.mask.eq(0),
            read_address.eq((position<<3)+Mux(f.ongoing('RX_SHIFT'),word_index+1,word_index)),
            tx.be.eq(Mux(remaining>=4,15,(Constant(1,5)<<remaining[:2])-1)),tx.error.eq(0),
            tx.data.eq(chunk[:32]),tx.last.eq(remaining<=4)]
        f.act('IDLE',
            If(pending & (~available_rx | (fault!=0)),release.eq(1),
                NextValue(self.rx_drops,self.rx_drops+1))
            .Elif(pending & (last_rx==0),NextValue(is_rx,1),NextValue(last_rx,1),
                NextValue(descaddr,rxbase+((rxprod & mask)<<5)),NextState('DESC_ADDR'))
            .Elif(enabled & (fault==0) & (txprod!=txcons),NextValue(is_rx,0),NextValue(last_rx,0),
                NextValue(descaddr,txbase+((txcons & mask)<<5)),NextState('DESC_ADDR'))
            .Elif(pending,NextValue(is_rx,1),NextValue(last_rx,1),
                NextValue(descaddr,rxbase+((rxprod & mask)<<5)),NextState('DESC_ADDR')))
        f.act('DESC_ADDR',NextValue(address,descaddr),NextState('DESC_REQ'))
        f.act('DESC_REQ',q.valid.eq(1),If(q.ready,NextState('DESC_WAIT')))
        f.act('DESC_WAIT',r.ready.eq(1),If(r.valid,NextValue(desc,r.data),NextState('CHECK')))
        f.act('CHECK',
            If(~flags[31],event.eq(4),NextValue(fault,2),NextState('IDLE'))
            .Else(NextValue(error,~valid_buffer|Mux(is_rx,length>capacity,
                    (declen<14)|(declen>self.mtu)|(declen>capacity))),
                NextValue(address,buf),NextValue(remaining,Mux(is_rx,length,declen)),
                NextValue(position,0),NextState('START')))
        f.act('START',If(error,event.eq(4),NextState('COMPLETE'))
            .Elif(is_rx,NextState('RX_LOAD')).Else(NextState('TX_REQ')))
        f.act('TX_REQ',q.valid.eq(1),If(q.ready,NextState('TX_WAIT')))
        f.act('TX_WAIT',r.ready.eq(1),If(r.valid,NextValue(chunk,r.data),
            NextValue(position,0),NextState('TX_PUSH')))
        f.act('TX_PUSH',tx.valid.eq(1),If(tx.ready,
            NextValue(chunk,chunk>>32),NextValue(remaining,Mux(remaining>4,remaining-4,0)),NextValue(position,position+4),
            If(remaining<=4,NextState('COMPLETE'))
            .Elif(position==28,NextValue(address,address+32),NextState('TX_REQ'))))
        f.act('RX_LOAD',NextValue(word_index,0),NextState('RX_PRIME'))
        f.act('RX_PRIME',NextState('RX_SHIFT'))
        f.act('RX_SHIFT',NextValue(chunk,Cat(chunk[32:],read_word)),
            If(word_index==7,NextState('RX_WRITE')).Else(NextValue(word_index,word_index+1)))
        f.act('RX_WRITE',q.valid.eq(1),q.write.eq(1),
            q.mask.eq(Mux(remaining>=32,0,~((Constant(1,33)<<remaining[:5])-1))),
            If(q.ready,NextState('RX_ACK')))
        f.act('RX_ACK',r.ready.eq(1),If(r.valid,
            If(remaining<=32,NextState('COMPLETE')).Else(NextValue(remaining,remaining-32),
                NextValue(position,position+1),NextValue(address,address+32),NextState('RX_LOAD'))))
        f.act('COMPLETE',NextValue(address,descaddr),
            NextValue(chunk,Cat(desc[:64],Mux(error,0,Mux(is_rx,length,declen)),
                (flags & 0x1fffffff)|0x40000000|(error<<29),desc[128:])),NextState('DONE_WRITE'))
        f.act('DONE_WRITE',q.valid.eq(1),q.write.eq(1),If(q.ready,NextState('DONE_ACK')))
        f.act('DONE_ACK',r.ready.eq(1),If(r.valid,
            event.eq(Mux(is_rx,2,1)|Mux(error,4,0)),
            If(is_rx,release.eq(1),NextValue(rxprod,rxprod+1),NextValue(self.rx_frames,self.rx_frames+1))
            .Else(NextValue(txcons,txcons+1),NextValue(self.tx_frames,self.tx_frames+1)),NextState('IDLE')))
