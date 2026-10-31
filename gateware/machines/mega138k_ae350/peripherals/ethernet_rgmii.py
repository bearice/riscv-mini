"""Gigabit GW5A RGMII PHY. RTL8211 must enable both internal 2ns delays.

IO timing models the PHY's delayed RX clock and delayed TX sampling clock.
The management plane provides the independent 25MHz reference and reset.
"""
from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer
from litex.gen import LiteXModule, Open
from litex.build.io import DDRInput, DDROutput
from litex.soc.interconnect import stream
from liteeth.common import eth_phy_description
from liteeth.phy.parallel.rgmii.gw5 import LiteEthPHYRGMIITX


class AE350EthTXPipe(stream.Buffer):
    pass


class AE350RGMIIRX(LiteXModule):
    def __init__(self, pads, default_taps, delay_taps, delay_load):
        self.source = source = stream.Endpoint(eth_phy_description(8))
        self.rx_er_frame_pulse = Signal()
        self.comb += source.be.eq(1)
        rx_ctl_delayf = Signal()
        rx_ctl = Signal(2)
        rx_data_delayf = Signal(4)
        rx_data = Signal(8)
        self.specials += [
            Instance('IODELAY',
                p_DYN_DLY_EN='TRUE', p_ADAPT_EN='FALSE', p_C_STATIC_DLY=default_taps,
                i_SDTAP=0, i_DLYSTEP=delay_taps[0], i_VALUE=delay_load,
                i_DI=pads.rx_ctl, o_DF=Open(), o_DO=rx_ctl_delayf),
            DDRInput(clk=ClockSignal('eth_rx'), i=rx_ctl_delayf, o1=rx_ctl[0], o2=rx_ctl[1]),
        ]
        for i in range(4):
            self.specials += [
                Instance('IODELAY',
                    p_DYN_DLY_EN='TRUE', p_ADAPT_EN='FALSE', p_C_STATIC_DLY=default_taps,
                    i_SDTAP=0, i_DLYSTEP=delay_taps[i+1], i_VALUE=delay_load,
                    i_DI=pads.rx_data[i], o_DF=Open(), o_DO=rx_data_delayf[i]),
                DDRInput(clk=ClockSignal('eth_rx'), i=rx_data_delayf[i], o1=rx_data[i], o2=rx_data[i+4]),
            ]
        rx_ctl_q = Signal(2)
        rx_data_q = Signal(8, reset_less=True)
        rx_ctl_d = Signal()
        byte_rx_er = Signal()
        frame_rx_er = Signal()
        last = Signal()
        self.sync += [
            rx_ctl_q.eq(rx_ctl),
            rx_data_q.eq(rx_data),
            rx_ctl_d.eq(rx_ctl_q[0]),
            source.valid.eq(rx_ctl_q[0]),
            source.data.eq(rx_data_q),
            byte_rx_er.eq(rx_ctl_q[0] & ~rx_ctl_q[1]),
            If(~rx_ctl_q[0],
                frame_rx_er.eq(0),
            ).Elif(~rx_ctl_q[1],
                frame_rx_er.eq(1),
            ),
            self.rx_er_frame_pulse.eq(last & frame_rx_er),
        ]
        self.comb += [
            last.eq(~rx_ctl_q[0] & rx_ctl_d),
            source.last.eq(last),
            source.error.eq(Mux(last, frame_rx_er, byte_rx_er)),
        ]


class EthernetRGMII(LiteXModule):
    dw = 8
    tx_clk_freq = 125e6
    rx_clk_freq = 125e6
    max_rx_tap = 240
    def __init__(self, platform, pads, clkin, phy_reset_n, clock_hz, rx_delay_ps=0):
        if not 0<=rx_delay_ps<=3000 or rx_delay_ps%100:
            raise ValueError("RX alignment must be 0..3000 ps in 100 ps steps")
        if clock_hz != 125000000:
            raise ValueError('RGMII packet backend currently requires gigabit/125MHz')
        self.default_rx_taps = int(round(rx_delay_ps / 12.5))
        if not 0 <= self.default_rx_taps <= self.max_rx_tap:
            raise ValueError('RX static tap out of range')
        self.rx_delay_taps = [Signal(8, reset=self.default_rx_taps) for _ in range(5)]
        self.rx_delay_load = Signal()
        init_load = Signal(reset=1)
        self.sync += init_load.eq(0)
        iodelay_value = Signal()
        self.comb += iodelay_value.eq(init_load | self.rx_delay_load)
        self.cd_eth_tx=ClockDomain('eth_tx');self.cd_eth_rx=ClockDomain('eth_rx')
        locked=Signal()
        self.comb += self.cd_eth_rx.clk.eq(pads.rx_clk)
        params=dict(p_FCLKIN='50.0',p_IDIV_SEL=1,p_FBDIV_SEL=1,p_MDIV_SEL=20,
            p_ODIV0_SEL=8,p_CLKFB_SEL='INTERNAL',p_CLK4_IN_SEL=Constant(0,2),
            p_RESET_I_EN='FALSE',p_RESET_O_EN='FALSE',p_SSC_EN='FALSE',
            i_CLKIN=clkin,i_CLKFB=0,i_RESET=ResetSignal('sys'),i_PLLPWD=0,
            i_RESET_I=0,i_RESET_O=0,o_LOCK=locked,o_CLKOUT0=self.cd_eth_tx.clk)
        for name,width in dict(FBDSEL=6,IDSEL=6,MDSEL=7,MDSEL_FRAC=3,ODSEL0_FRAC=3,
            PSSEL=3,PSDIR=1,PSPULSE=1,SSCPOL=1,SSCON=1,SSCMDSEL=7,
            SSCMDSEL_FRAC=3,ICPSEL=6,LPFRES=3,LPFCAP=2).items():
            params['i_'+name]=Constant(0,width)
        for i in range(7):params.update({'p_CLKOUT%d_EN'%i:'TRUE' if i==0 else 'FALSE',
            'i_ENCLK%d'%i:1,'i_ODSEL%d'%i:Constant(0,7)})
        self.specials += Instance('PLL',name='ae350_eth_pll',**params)
        self.locked=locked
        self.rx_clock_events=Signal(32)
        from migen.genlib.cdc import MultiReg
        divider=Signal(8);sampled=Signal();previous=Signal()
        self.sync.eth_rx += divider.eq(divider+1)
        self.specials += MultiReg(divider[7],sampled,'sys')
        self.sync += [previous.eq(sampled),If(sampled!=previous,self.rx_clock_events.eq(self.rx_clock_events+1))]
        reset=ResetSignal('sys')|~locked|~phy_reset_n
        tx_rst_meta=Signal()
        self.specials += [
            Instance('DFFP',name='ae350_eth_tx_rst_ff0',i_D=0,i_PRESET=reset,i_CLK=self.cd_eth_tx.clk,o_Q=tx_rst_meta),
            Instance('DFFP',name='ae350_eth_tx_rst_ff1',i_D=tx_rst_meta,i_PRESET=reset,i_CLK=self.cd_eth_tx.clk,o_Q=self.cd_eth_tx.rst),
        ]
        self.specials += AsyncResetSynchronizer(self.cd_eth_rx,reset)
        self.tx=ClockDomainsRenamer('eth_tx')(LiteEthPHYRGMIITX(pads))
        self.rx=ClockDomainsRenamer('eth_rx')(AE350RGMIIRX(pads,self.default_rx_taps,self.rx_delay_taps,iodelay_value))
        self.sink,self.source=self.tx.sink,self.rx.source
        from migen.genlib.cdc import PulseSynchronizer
        self.phy_rx_frames,self.phy_tx_frames,self.phy_rx_er_frames=Signal(32),Signal(32),Signal(32)
        self.rx_event=PulseSynchronizer('eth_rx','sys')
        self.tx_event=PulseSynchronizer('eth_tx','sys')
        self.rx_er_event=PulseSynchronizer('eth_rx','sys')
        self.comb += [self.rx_event.i.eq(self.source.valid & self.source.last),
                      self.tx_event.i.eq(self.sink.valid & self.sink.last),
                      self.rx_er_event.i.eq(self.rx.rx_er_frame_pulse)]
        self.sync += [If(self.rx_event.o,self.phy_rx_frames.eq(self.phy_rx_frames+1)),
                      If(self.tx_event.o,self.phy_tx_frames.eq(self.phy_tx_frames+1)),
                      If(self.rx_er_event.o,self.phy_rx_er_frames.eq(self.phy_rx_er_frames+1))]
        clock_out=Signal()
        self.specials += [DDROutput(clk=ClockSignal('eth_tx'),i1=1,i2=0,o=clock_out),
            Instance('IODELAY',p_DYN_DLY_EN='FALSE',p_ADAPT_EN='FALSE',p_C_STATIC_DLY=0,
                i_SDTAP=0,i_DLYSTEP=Constant(0,8),i_VALUE=0,i_DI=clock_out,o_DF=Open(),o_DO=pads.tx_clk)]
        platform.add_period_constraint(pads.rx_clk,8,name='eth_rx_clk')
        platform.add_generated_clock_constraint(self.cd_eth_tx.clk,clkin,
            divide_by=8,multiply_by=20,pin='ae350_eth_pll/CLKOUT0')
        tx_ports='[get_ports {ethernet_management_tx_data* ethernet_management_tx_ctl ethernet_management_tx_clk}]'
        rx_ports='[get_ports {ethernet_management_rx_data* ethernet_management_rx_ctl}]'
        self.constraints=[]
        for edge in ('',' -clock_fall -add_delay'):
            for mode,value in (('max',3),('min',1)):
                self.constraints.append('set_input_delay -clock eth_rx_clk -%s %d%s %s'%(mode,value,edge,rx_ports))
            # Select opposite output transition polarities on the two report
            # capture edges. Gowin otherwise merges them into one worst arc.
            # With max_delay32/min_delay-16, the selected paths retain the
            # original absolute [0,16]ns report bounds; the other transition
            # is relaxed only to make both polarities visible in the report.
            # Actual bus acceptance remains the mandatory routed skew gate.
            for mode in ('max','min'):
                for polarity in ('rise','fall'):
                    selected=(polarity=='rise') != bool(edge)
                    value=(16 if mode=='max' else -16) if selected else 0
                    self.constraints.append('set_output_delay -clock eth_tx_clk -%s %d -%s%s -add_delay %s'
                        %(mode,value,polarity,edge.replace(' -add_delay',''),tx_ports))
        # These reporting bounds expose actual clock/data output paths.
        # The mandatory post-route gate checks Table60's +/-0.5ns TX skew
        # and45..55% duty, not an ideal zero-insertion external clock.
        # RX multicycle exceptions (setup=1, hold=0) preserve the exact 4.0ns/0.0ns
        # DDR edge relationships while promoting all 5 RX lanes (both edges) into
        # Section 3.7 alongside TX under a single global report_exceptions pair.
        self.constraints += [
            'set_max_delay -from [get_clocks {eth_tx_clk}] -to '+tx_ports+' 32',
            'set_min_delay -from [get_clocks {eth_tx_clk}] -to '+tx_ports+' -16',
            'set_multicycle_path -from '+rx_ports+' -setup -end 1',
            'set_multicycle_path -from '+rx_ports+' -hold -end 0',
            'report_timing -setup -max_paths 100 -max_common_paths 4',
            'report_exceptions -setup -max_paths 256 -max_common_paths 16',
            'report_timing -hold -max_paths 100 -max_common_paths 4',
            'report_exceptions -hold -max_paths 256 -max_common_paths 16',
        ]


def add_mac_timing_buffers(mac, platform=None):
    """Keep complete TX frames in sys RAM; isolate both CDC ready paths."""
    from litex.soc.interconnect import stream
    from litex.soc.interconnect.packet import PacketFIFO
    from liteeth.mac.core import PacketDropFIFO
    from liteeth.common import eth_phy_description, eth_packet_fifo_depth
    dw=len(mac.sink.data)
    datapath=mac.tx_datapath
    if dw==32:
        # Formatting runs at sys50/32bits. A complete frame is retained before
        # crossing to125MHz, whose only storage is the small asynchronous FIFO
        # and word serializer. No125MHz synchronous BSRAM is required.
        old=[p for p in datapath.pipeline if isinstance(p,PacketFIFO)]
        if len(old)!=1:raise ValueError('Unsupported LiteEth TX packet pipeline')
        datapath.pipeline.remove(old[0])
        modules=[(name,module) for name,module in datapath._submodules if module is not old[0]]
        if len(modules)!=len(datapath._submodules)-1:raise ValueError('TX FIFO module not found')
        datapath._submodules=modules
        datapath.sys_packet_fifo=PacketFIFO(eth_phy_description(32),
            payload_depth=old[0].payload_fifo.depth//4,param_depth=4,buffered=True)
        cdc_indices=[i for i,p in enumerate(datapath.pipeline) if isinstance(p,stream.ClockDomainCrossing)]
        if len(cdc_indices)!=1:raise ValueError('Unsupported LiteEth TX CDC layout')
        datapath.pipeline.insert(cdc_indices[0],datapath.sys_packet_fifo)
    elif dw!=8:raise ValueError('Unsupported MAC DMA data width')
    indices=[i for i,stage in enumerate(datapath.pipeline) if isinstance(stage,stream.ClockDomainCrossing)]
    if len(indices)!=1:raise ValueError('Unsupported LiteEth TX CDC pipeline layout')
    cdc=datapath.pipeline[indices[0]]
    if dw==32:
        # Keep the small dual-clock queue in registers (the device has no SSRAM).
        # Otherwise Gowin absorbs its output register into BSRAM with a
        #125MHz read clock, reintroducing the BSRAM minimum-pulse failure.
        from migen.fhdl.structure import _Assign
        from migen.fhdl.specials import Memory
        from migen.genlib.cdc import MultiReg
        from functools import reduce
        from operator import or_
        queues=[module for _,module in cdc._submodules if isinstance(module,stream.AsyncFIFO)]
        if len(queues)!=1:raise ValueError('Unsupported TX asynchronous FIFO layout')
        memories=[special for special in queues[0].fifo._fragment.specials if isinstance(special,Memory)]
        if len(memories)!=1:raise ValueError('Unsupported TX FIFO storage layout')
        memories[0].name_override='ae350_eth_tx_cdc_ram'
        for sp in list(queues[0].fifo._fragment.specials):
            if isinstance(sp,MultiReg) and sp.odomain=='read':
                queues[0].fifo._fragment.specials.remove(sp)
                sync0=Signal.like(sp.i,name_override='ae350_eth_tx_cdc_sync0',reset_less=True)
                sync1=Signal.like(sp.i,name_override='ae350_eth_tx_cdc_sync1',reset_less=True)
                sync0.attr.add('no_retiming');sync1.attr.add('no_retiming')
                queues[0].fifo.sync.read += [sync0.eq(sp.i),sync1.eq(sync0)]
                queues[0].fifo.comb += sp.o.eq(sync1)
                for stmt in list(queues[0].fifo._fragment.comb):
                    if isinstance(stmt,_Assign) and stmt.l is queues[0].fifo.readable:
                        queues[0].fifo._fragment.comb.remove(stmt)
                        consume_q=stmt.r.operands[0]
                        cdc_diff=Signal.like(sp.i,name_override='ae350_eth_tx_cdc_diff')
                        cdc_diff.attr.add(('syn_keep',1))
                        queues[0].fifo.comb += [
                            cdc_diff.eq(consume_q ^ sync1),
                            queues[0].fifo.readable.eq(reduce(or_,[cdc_diff[i] for i in range(len(cdc_diff))])),
                        ]
        if platform is not None:
            # SUG550 §5.16: apply to this RAM only; retain block RAM for the
            # large sys-domain packet queues and the external-clock RX FIFO.
            platform.toolchain.additional_tcl_commands += [
                'set eth_ram_constraints [open ae350_ethernet.gsc w]',
                'puts $eth_ram_constraints {INS "ae350_eth_tx_cdc_ram" syn_ramstyle=registers}',
                'close $eth_ram_constraints',
                'add_file ae350_ethernet.gsc',
            ]
    datapath.cdc_output_buffer=ClockDomainsRenamer('eth_tx')(
        AE350EthTXPipe(cdc.source.description,pipe_valid=True,pipe_ready=True))
    datapath.pipeline.insert(indices[0]+1,datapath.cdc_output_buffer)
    for datapath,kind,domain,ready in ((mac.tx_datapath,PacketFIFO,'sys' if dw==32 else 'eth_tx',True),
                                       (mac.rx_datapath,PacketDropFIFO,'eth_rx',False)):
        indices=[i for i,stage in enumerate(datapath.pipeline) if isinstance(stage,kind)]
        if len(indices)!=1:raise ValueError('Unsupported LiteEth packet FIFO pipeline layout')
        description=datapath.pipeline[indices[0]].sink.description
        buffer=ClockDomainsRenamer(domain)(stream.Buffer(description,pipe_valid=True,pipe_ready=ready))
        datapath.packet_input_buffer=buffer
        datapath.pipeline.insert(indices[0],buffer)
        if domain=='eth_rx':
            datapath.cdc_input_buffer=ClockDomainsRenamer('eth_rx')(
                stream.Buffer(description,pipe_valid=True,pipe_ready=True))
            datapath.pipeline.insert(indices[0]+2,datapath.cdc_input_buffer)
    from liteeth.mac.crc import LiteEthMACCRC32Checker
    crc_stages=[p for p in mac.rx_datapath.pipeline if isinstance(p,LiteEthMACCRC32Checker)]
    if len(crc_stages)!=1:raise ValueError('Unsupported LiteEth RX CRC pipeline layout')
    rx_crc=crc_stages[0]
    mac.rx_word_rx_er=Signal(4)
    mac.rx_fcs_rx_er=Signal(4)
    mac.rx_frame_fcs_error=Signal()
    mac.comb += [
        mac.rx_word_rx_er.eq(rx_crc.fifo.source.error & mac.source.be),
        mac.rx_fcs_rx_er.eq(rx_crc.fifo.sink.error & rx_crc.fifo.sink.be),
        mac.rx_frame_fcs_error.eq(rx_crc.error),
    ]
