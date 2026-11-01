"""10GBASE-R SFP+ Subsystem for Tang Mega 138K Pro.

Integrates Gowin SerDes Quad 1 (Lane 0) + Gowin 10G Ethernet MAC IP.
Reference clock is external 125 MHz differential input from onboard MS5351.
Fabric XGMII and MAC streaming logic operate at 156.25 MHz (Xge_pll).
Includes hardware line-rate frame generator, receiver monitor, and APB registers.
"""
from pathlib import Path
from migen import *
from migen.genlib.cdc import MultiReg
from litex.gen import LiteXModule


def add_sfp_sources(platform):
    """Add encrypted vendor Verilog sources for SerDes and 10G MAC."""
    root = Path(__file__).resolve().parents[4]
    vendor_dir = root / 'gateware/vendor/gowin_gw5ast'
    sources = [
        vendor_dir / 'sfp_pll_156m/gowin_pll.v',
        vendor_dir / 'sfp_mac_10g/Ten_Giga_Ethernet_MAC.v',
        vendor_dir / 'sfp_serdes/serdes.v',
        vendor_dir / 'sfp_serdes/ten_giga_serial_ethernet/ten_giga_serial_ethernet.v',
        vendor_dir / 'sfp_serdes/ten_giga_serial_ethernet_1/ten_giga_serial_ethernet_1.v',
    ]
    for src in sources:
        platform.add_source(str(src))
    platform.add_verilog_include_path(str(vendor_dir / 'sfp_serdes'))
    platform.add_verilog_include_path(str(vendor_dir / 'sfp_serdes/ten_giga_serial_ethernet'))
    platform.add_verilog_include_path(str(vendor_dir / 'sfp_serdes/ten_giga_serial_ethernet_1'))
    platform.add_verilog_include_path(str(vendor_dir / 'sfp_mac_10g'))
    platform.add_verilog_include_path(str(vendor_dir / 'sfp_pll_156m'))

    # Add SerDes configuration registers and PnR optimization options for Gowin PnR
    csr_file = vendor_dir / 'sfp_serdes/serdes.csr'
    if hasattr(platform, 'toolchain'):
        if hasattr(platform.toolchain, 'additional_tcl_commands'):
            platform.toolchain.additional_tcl_commands.extend([
                f'add_file "{csr_file}"',
                f'set_csr "{csr_file}"',
                'set_option -serdesRetiming 1',
                'set_option -replicate_resources 1',
            ])
        if hasattr(platform.toolchain, 'additional_cst_commands'):
            platform.toolchain.additional_cst_commands.extend([
                'INS_LOC "Xge_pll/PLL_inst" PLL_L[1];',
                'CLOCK_LOC "ethernetsfp10g_xge_clk" BUFG = CLK;',
            ])


class EthernetSFP10G(LiteXModule):
    """SFP0 10GBASE-R controller with line-rate packet engine and APB CSRs."""
    def __init__(self, platform, sfp0_pads, sfp1_pads=None):
        self.off = Signal(12)
        self.wdata = Signal(32)
        self.rdata = Signal(32)
        self.selected = Signal()
        self.write = Signal()
        self.commit = Signal()
        self.ok = Signal()
        self.irq = Signal()

        # Add vendor IP sources
        add_sfp_sources(platform)

        # Timing constraints for XGMII and SerDes clocks
        self.constraints = [
            'create_clock -name xgmii_clk -period 6.400 -waveform {0 3.200} [get_pins {Xge_pll/PLL_inst/CLKOUT0}]',
            'create_clock -name serdes1_tx_clk -period 6.173 -waveform {0 3.087} [get_nets {u_sfp_serdes/q1_lane0_fabric_tx_clk}]',
            'create_clock -name serdes1_rx_clk -period 6.173 -waveform {0 3.087} [get_nets {u_sfp_serdes/q1_lane0_fabric_rx_clk}]',
            'create_clock -name serdes2_tx_clk -period 6.173 -waveform {0 3.087} [get_nets {u_sfp_serdes/q1_lane1_fabric_tx_clk}]',
            'create_clock -name serdes2_rx_clk -period 6.173 -waveform {0 3.087} [get_nets {u_sfp_serdes/q1_lane1_fabric_rx_clk}]',
        ]

        # ---------------------------------------------------------------------
        # Clock & Reset Generation
        # ---------------------------------------------------------------------
        serdes_refclk = Signal()
        xge_clk = Signal()
        xge_clk_lock = Signal()

        self.specials += Instance('Xge_pll',
            i_clkin=serdes_refclk,
            o_clkout0=xge_clk,
            o_lock=xge_clk_lock,
        )

        # ---------------------------------------------------------------------
        # SerDes & 10G PCS Wires
        # ---------------------------------------------------------------------
        sfp0_los_synced = Signal()
        self.specials += MultiReg(sfp0_pads.los, sfp0_los_synced, 'sys')

        ctrl_tx_disable = Signal(reset=0)
        ctrl_sig_detect_ovr = Signal(reset=0)
        ctrl_loopback = Signal(reset=0)
        ctrl_pkt_gen_arm = Signal()
        ctrl_pkt_gen_count = Signal(16, reset=10)
        ctrl_clear_stats = Signal()
        ctrl_dma_en = Signal(reset=1)
        self.dma_en = ctrl_dma_en

        signal_detected = Signal()
        self.comb += signal_detected.eq(Mux(ctrl_sig_detect_ovr, 1, ~sfp0_los_synced))
        self.comb += sfp0_pads.tx_disable.eq(ctrl_tx_disable)
        if sfp1_pads is not None:
            self.comb += sfp1_pads.tx_disable.eq(1)

        xgmii_txc = Signal(8)
        xgmii_txd = Signal(64)
        xgmii_rxc = Signal(8)
        xgmii_rxd = Signal(64)

        block_lock = Signal()
        hi_ber = Signal()
        pcs_status = Signal()
        ber_count = Signal(6)
        errored_block_count = Signal(7)
        debug_vector = Signal(16)

        serdes_rstn = Signal()
        self.comb += serdes_rstn.eq(xge_clk_lock)

        self.specials += Instance('SerDes_Top',
            name='u_sfp_serdes',
            # Channel 0 (Lane 0 / SFP0 10GBASE-R)
            o_Ten_Giga_Serial_Ethernet_Top_ref_clk_o=serdes_refclk,
            o_Ten_Giga_Serial_Ethernet_Top_clk_out0_o=Signal(),
            o_Ten_Giga_Serial_Ethernet_Top_clk_out1_o=Signal(),
            i_Ten_Giga_Serial_Ethernet_Top_rx_rstn_i=serdes_rstn,
            i_Ten_Giga_Serial_Ethernet_Top_tx_rstn_i=serdes_rstn,
            i_Ten_Giga_Serial_Ethernet_Top_signal_detect_i=signal_detected,
            i_Ten_Giga_Serial_Ethernet_Top_xgmii_rx_clk_i=xge_clk,
            i_Ten_Giga_Serial_Ethernet_Top_xgmii_tx_clk_i=xge_clk,
            i_Ten_Giga_Serial_Ethernet_Top_xgmii_rx_clk_ready_i=xge_clk_lock,
            i_Ten_Giga_Serial_Ethernet_Top_xgmii_tx_clk_ready_i=xge_clk_lock,
            i_Ten_Giga_Serial_Ethernet_Top_xgmii_txc_i=xgmii_txc,
            i_Ten_Giga_Serial_Ethernet_Top_xgmii_txd_i=xgmii_txd,
            o_Ten_Giga_Serial_Ethernet_Top_xgmii_rxc_o=xgmii_rxc,
            o_Ten_Giga_Serial_Ethernet_Top_xgmii_rxd_o=xgmii_rxd,
            o_Ten_Giga_Serial_Ethernet_Top_block_lock_o=block_lock,
            o_Ten_Giga_Serial_Ethernet_Top_hi_ber_o=hi_ber,
            o_Ten_Giga_Serial_Ethernet_Top_pcs_status_o=pcs_status,
            o_Ten_Giga_Serial_Ethernet_Top_ber_count_o=ber_count,
            o_Ten_Giga_Serial_Ethernet_Top_errored_block_count_o=errored_block_count,
            o_Ten_Giga_Serial_Ethernet_Top_debug_vector_o=debug_vector,
            i_Ten_Giga_Serial_Ethernet_Top_clear_ber_count_i=ctrl_clear_stats,
            i_Ten_Giga_Serial_Ethernet_Top_clear_errored_block_count_i=ctrl_clear_stats,
            # Channel 1 (Lane 1 tied off in reset)
            o_Ten_Giga_Serial_Ethernet_Top_1_ref_clk_o=Signal(),
            o_Ten_Giga_Serial_Ethernet_Top_1_clk_out0_o=Signal(),
            o_Ten_Giga_Serial_Ethernet_Top_1_clk_out1_o=Signal(),
            i_Ten_Giga_Serial_Ethernet_Top_1_rx_rstn_i=0,
            i_Ten_Giga_Serial_Ethernet_Top_1_tx_rstn_i=0,
            i_Ten_Giga_Serial_Ethernet_Top_1_signal_detect_i=0,
            i_Ten_Giga_Serial_Ethernet_Top_1_xgmii_rx_clk_i=xge_clk,
            i_Ten_Giga_Serial_Ethernet_Top_1_xgmii_tx_clk_i=xge_clk,
            i_Ten_Giga_Serial_Ethernet_Top_1_xgmii_rx_clk_ready_i=0,
            i_Ten_Giga_Serial_Ethernet_Top_1_xgmii_tx_clk_ready_i=0,
            i_Ten_Giga_Serial_Ethernet_Top_1_xgmii_txc_i=0,
            i_Ten_Giga_Serial_Ethernet_Top_1_xgmii_txd_i=0,
            o_Ten_Giga_Serial_Ethernet_Top_1_xgmii_rxc_o=Signal(8),
            o_Ten_Giga_Serial_Ethernet_Top_1_xgmii_rxd_o=Signal(64),
            o_Ten_Giga_Serial_Ethernet_Top_1_block_lock_o=Signal(),
            o_Ten_Giga_Serial_Ethernet_Top_1_hi_ber_o=Signal(),
            o_Ten_Giga_Serial_Ethernet_Top_1_pcs_status_o=Signal(),
            o_Ten_Giga_Serial_Ethernet_Top_1_ber_count_o=Signal(6),
            o_Ten_Giga_Serial_Ethernet_Top_1_errored_block_count_o=Signal(7),
            o_Ten_Giga_Serial_Ethernet_Top_1_debug_vector_o=Signal(16),
            i_Ten_Giga_Serial_Ethernet_Top_1_clear_ber_count_i=0,
            i_Ten_Giga_Serial_Ethernet_Top_1_clear_errored_block_count_i=0,
        )

        # ---------------------------------------------------------------------
        # 10G Ethernet MAC Instance
        # ---------------------------------------------------------------------
        mac_rstn = Signal()
        self.comb += mac_rstn.eq(block_lock & serdes_rstn)

        rx_mac_clk = Signal()
        rx_mac_valid = Signal()
        rx_mac_data = Signal(64)
        rx_mac_byte = Signal(8)
        rx_mac_last = Signal()
        rx_mac_error = Signal()

        tx_mac_clk = Signal()
        tx_mac_valid = Signal()
        tx_mac_data = Signal(64)
        tx_mac_byte = Signal(8)
        tx_mac_last = Signal()
        tx_mac_error = Signal()
        tx_mac_ready = Signal()

        local_fault = Signal()
        remote_fault = Signal()

        self.specials += Instance('Ten_Giga_Ethernet_MAC_Top',
            name='u_sfp_10g_mac',
            i_rx_rstn_i=mac_rstn,
            i_tx_rstn_i=mac_rstn,
            i_xgmii_rx_clk_i=xge_clk,
            i_xgtx_clk_i=xge_clk,
            i_xgmii_rxc_i=xgmii_rxc,
            i_xgmii_rxd_i=xgmii_rxd,
            o_xgmii_txc_o=xgmii_txc,
            o_xgmii_txd_o=xgmii_txd,
            i_rx_fcs_fwd_ena_i=0,
            i_rx_jumbo_ena_i=1,
            i_tx_fcs_fwd_ena_i=0,
            i_tx_fault_ena_i=0,
            i_tx_ifg_delay_ena_i=0,
            i_tx_ifg_delay_i=3,
            o_rx_mac_clk_o=rx_mac_clk,
            o_rx_mac_valid_o=rx_mac_valid,
            o_rx_mac_data_o=rx_mac_data,
            o_rx_mac_byte_o=rx_mac_byte,
            o_rx_mac_last_o=rx_mac_last,
            o_rx_mac_error_o=rx_mac_error,
            o_rx_statistics_valid_o=Signal(),
            o_rx_statistics_vector_o=Signal(20),
            o_tx_mac_clk_o=tx_mac_clk,
            i_tx_mac_valid_i=tx_mac_valid,
            i_tx_mac_data_i=tx_mac_data,
            i_tx_mac_byte_i=tx_mac_byte,
            i_tx_mac_last_i=tx_mac_last,
            i_tx_mac_error_i=tx_mac_error,
            o_tx_mac_ready_o=tx_mac_ready,
            o_tx_statistics_valid_o=Signal(),
            o_tx_statistics_vector_o=Signal(19),
            o_local_fault_o=local_fault,
            o_remote_fault_o=remote_fault,
        )

        # ---------------------------------------------------------------------
        # Line-Rate Hardware Packet Generator & Receiver Counters
        # ---------------------------------------------------------------------
        # Statistics counters (in 156.25 MHz domain, synced to sys)
        rx_frame_cnt = Signal(32)
        tx_frame_cnt = Signal(32)
        rx_byte_cnt = Signal(32)
        tx_byte_cnt = Signal(32)
        rx_err_cnt = Signal(32)

        # Simple hardware frame generator
        pkt_gen_busy = Signal()
        pkt_gen_words = Signal(8, reset=18) # 18 words * 8 = 144 bytes per test packet
        pkt_gen_word_idx = Signal(8)
        pkt_gen_rem_pkts = Signal(16)
        pkt_gen_armed_sync = Signal()
        self.specials += MultiReg(ctrl_pkt_gen_arm, pkt_gen_armed_sync, 'eth_sfp_tx')

        gen_tx_valid = Signal()
        gen_tx_data  = Signal(64)
        gen_tx_byte  = Signal(8)
        gen_tx_last  = Signal()

        # Create clock domain for SFP 10G TX & RX (both synchronous to xge_clk)
        self.clock_domains.cd_eth_sfp_tx = ClockDomain('eth_sfp_tx')
        self.clock_domains.cd_eth_sfp_rx = ClockDomain('eth_sfp_rx')
        self.comb += [
            self.cd_eth_sfp_tx.clk.eq(tx_mac_clk),
            self.cd_eth_sfp_tx.rst.eq(~mac_rstn),
            self.cd_eth_sfp_rx.clk.eq(rx_mac_clk),
            self.cd_eth_sfp_rx.rst.eq(~mac_rstn),
        ]

        # Packet Generator Registers & State Machine
        GEN_IDLE = 0
        GEN_SEND = 1
        GEN_GAP  = 2
        gen_state = Signal(2, reset=GEN_IDLE)

        packet_words = [
            0x0002fa960bb4be60,
            0x0045000801001000,
            0x1140004000008000,
            0x020aa8c000000000,
            0x401f401f010aa8c0,
            0x0100000000006c00,
            0x0807060504030201,
            0x100f0e0d0c0b0a09,
            0x1817161514131211,
            0x201f1e1d1c1b1a19,
            0x2827262524232221,
            0x302f2e2d2c2b2a29,
            0x3837363534333231,
            0x403f3e3d3c3b3a39,
            0x4847464544434241,
            0x504f4e4d4c4b4a49,
            0x5857565554535251,
            0x605f5e5d5c5b5a59,
        ]
        word_lut = Array([Constant(w, 64) for w in packet_words])

        gen_armed_d = Signal()
        gen_trigger = Signal()
        self.sync.eth_sfp_tx += [
            gen_armed_d.eq(pkt_gen_armed_sync),
            gen_trigger.eq(pkt_gen_armed_sync & ~gen_armed_d),

            If(ctrl_clear_stats,
                tx_frame_cnt.eq(0),
                tx_byte_cnt.eq(0),
            ).Elif(tx_mac_valid & tx_mac_ready,
                tx_byte_cnt.eq(tx_byte_cnt + 8),
                If(tx_mac_last,
                    tx_frame_cnt.eq(tx_frame_cnt + 1)
                )
            ),

            Case(gen_state, {
                GEN_IDLE: [
                    pkt_gen_busy.eq(0),
                    gen_tx_valid.eq(0),
                    gen_tx_last.eq(0),
                    gen_tx_byte.eq(0),
                    If(gen_trigger,
                        pkt_gen_busy.eq(1),
                        pkt_gen_rem_pkts.eq(ctrl_pkt_gen_count),
                        pkt_gen_word_idx.eq(0),
                        gen_tx_valid.eq(1),
                        gen_tx_byte.eq(0xff),
                        gen_tx_data.eq(word_lut[0]),
                        gen_tx_last.eq(0),
                        gen_state.eq(GEN_SEND),
                    )
                ],
                GEN_SEND: [
                    pkt_gen_busy.eq(1),
                    If(tx_mac_ready,
                        If(pkt_gen_word_idx == 17,
                            gen_tx_valid.eq(0),
                            gen_tx_last.eq(0),
                            pkt_gen_word_idx.eq(0),
                            If(pkt_gen_rem_pkts <= 1,
                                pkt_gen_busy.eq(0),
                                gen_state.eq(GEN_IDLE),
                            ).Else(
                                pkt_gen_rem_pkts.eq(pkt_gen_rem_pkts - 1),
                                gen_state.eq(GEN_GAP),
                            )
                        ).Else(
                            pkt_gen_word_idx.eq(pkt_gen_word_idx + 1),
                            gen_tx_data.eq(word_lut[pkt_gen_word_idx + 1]),
                            gen_tx_byte.eq(0xff),
                            If(pkt_gen_word_idx == 16,
                                gen_tx_last.eq(1)
                            ).Else(
                                gen_tx_last.eq(0)
                            )
                        )
                    )
                ],
                GEN_GAP: [
                    pkt_gen_busy.eq(1),
                    gen_tx_valid.eq(0),
                    gen_tx_last.eq(0),
                    pkt_gen_word_idx.eq(pkt_gen_word_idx + 1),
                    If(pkt_gen_word_idx == 3,
                        pkt_gen_word_idx.eq(0),
                        gen_tx_valid.eq(1),
                        gen_tx_byte.eq(0xff),
                        gen_tx_data.eq(word_lut[0]),
                        gen_tx_last.eq(0),
                        gen_state.eq(GEN_SEND),
                    )
                ]
            })
        ]

        # ---------------------------------------------------------------------
        # DMA Bridge Integration (32-bit sys @ 50 MHz <-> 64-bit 10G MAC @ 156.25 MHz)
        # ---------------------------------------------------------------------
        from gateware.machines.mega138k_ae350.peripherals.sfp_bridge import SFP10GDMABridge
        self.submodules.bridge = bridge = SFP10GDMABridge(depth=512)
        self.dma_sink = bridge.dma_sink
        self.dma_source = bridge.dma_source

        # RX: feed both statistics monitor and DMA bridge
        self.comb += [
            bridge.rx_mac_valid.eq(rx_mac_valid),
            bridge.rx_mac_data.eq(rx_mac_data),
            bridge.rx_mac_byte.eq(rx_mac_byte),
            bridge.rx_mac_last.eq(rx_mac_last),
            bridge.rx_mac_error.eq(rx_mac_error),
        ]

        # TX: Mux between hardware packet generator and DMA bridge
        self.comb += [
            If(pkt_gen_busy,
                tx_mac_valid.eq(gen_tx_valid),
                tx_mac_data.eq(gen_tx_data),
                tx_mac_byte.eq(gen_tx_byte),
                tx_mac_last.eq(gen_tx_last),
                tx_mac_error.eq(0),
                bridge.tx_mac_ready.eq(0),
            ).Else(
                tx_mac_valid.eq(bridge.tx_mac_valid),
                tx_mac_data.eq(bridge.tx_mac_data),
                tx_mac_byte.eq(bridge.tx_mac_byte),
                tx_mac_last.eq(bridge.tx_mac_last),
                tx_mac_error.eq(bridge.tx_mac_error),
                bridge.tx_mac_ready.eq(tx_mac_ready),
            )
        ]

        self.sync.eth_sfp_rx += [
            If(ctrl_clear_stats,
                rx_frame_cnt.eq(0),
                rx_byte_cnt.eq(0),
                rx_err_cnt.eq(0),
            ).Elif(rx_mac_valid,
                rx_byte_cnt.eq(rx_byte_cnt + 8),
                If(rx_mac_error, rx_err_cnt.eq(rx_err_cnt + 1)),
                If(rx_mac_last,
                    rx_frame_cnt.eq(rx_frame_cnt + 1)
                )
            )
        ]

        # ---------------------------------------------------------------------
        # Synchronize Status Signals into System Clock Domain (50 MHz)
        # ---------------------------------------------------------------------
        block_lock_sys = Signal()
        pcs_status_sys = Signal()
        hi_ber_sys = Signal()
        local_fault_sys = Signal()
        remote_fault_sys = Signal()
        self.specials += [
            MultiReg(block_lock, block_lock_sys),
            MultiReg(pcs_status, pcs_status_sys),
            MultiReg(hi_ber, hi_ber_sys),
            MultiReg(local_fault, local_fault_sys),
            MultiReg(remote_fault, remote_fault_sys),
        ]

        self.pcs_status_sys = pcs_status_sys
        self.sfp0_los_synced = sfp0_los_synced

        rx_frames_sys = Signal(32)
        tx_frames_sys = Signal(32)
        rx_bytes_sys = Signal(32)
        tx_bytes_sys = Signal(32)
        rx_errs_sys = Signal(32)
        self.specials += [
            MultiReg(rx_frame_cnt, rx_frames_sys),
            MultiReg(tx_frame_cnt, tx_frames_sys),
            MultiReg(rx_byte_cnt, rx_bytes_sys),
            MultiReg(tx_byte_cnt, tx_bytes_sys),
            MultiReg(rx_err_cnt, rx_errs_sys),
        ]

        # ---------------------------------------------------------------------
        # APB Target Registers (System Domain)
        # ---------------------------------------------------------------------
        # Offset Map:
        # 0x00: Identification 0xae35010a ("AE350 10G SFP")
        # 0x04: Control (W/R)
        #       bit 0: tx_disable
        #       bit 1: signal_detect override (force presence)
        #       bit 2: loopback enable
        #       bit 3: packet generator arm / trigger
        #       bit 4: clear statistics
        #       bit 5: dma_en (route EthernetDMA to SFP 10G; 1=enabled, 0=RGMII)
        #       bits [31:16]: packet generator count (frames to send)
        # 0x08: Status (RO)
        #       bit 0: pll_lock (Xge_pll lock)
        #       bit 1: block_lock (10GBASE-R 64b/66b frame locked)
        #       bit 2: pcs_status (10G PCS link normal)
        #       bit 3: sfp_los (optical module loss of signal pin)
        #       bit 4: hi_ber (high bit error rate)
        #       bit 5: local_fault
        #       bit 6: remote_fault
        #       bit 7: pkt_gen_busy
        #       bit 8: dma_en status
        # 0x0C: Error vector (RO: ber_count [5:0], errored_blocks [15:8])
        # 0x10: RX frame counter
        # 0x14: TX frame counter
        # 0x18: RX byte counter
        # 0x1C: TX byte counter
        # 0x20: RX error counter
        status_word = Signal(32)
        self.comb += status_word.eq(Cat(
            xge_clk_lock,       # bit 0
            block_lock_sys,     # bit 1
            pcs_status_sys,     # bit 2
            sfp0_los_synced,    # bit 3
            hi_ber_sys,         # bit 4
            local_fault_sys,    # bit 5
            remote_fault_sys,   # bit 6
            pkt_gen_busy,       # bit 7
            ctrl_dma_en,        # bit 8
            Constant(0, 23)
        ))

        err_word = Signal(32)
        self.comb += err_word.eq(Cat(ber_count, Constant(0, 2), errored_block_count, Constant(0, 17)))

        ctrl_word = Signal(32)
        self.comb += ctrl_word.eq(Cat(
            ctrl_tx_disable,     # bit 0
            ctrl_sig_detect_ovr, # bit 1
            ctrl_loopback,       # bit 2
            ctrl_pkt_gen_arm,    # bit 3
            ctrl_clear_stats,    # bit 4
            ctrl_dma_en,         # bit 5
            Constant(0, 10),
            ctrl_pkt_gen_count,  # bits 16..31
        ))

        self.comb += [
            self.ok.eq(self.selected & Mux(self.write,
                (self.off == 0x04),
                (self.off <= 0x24) & (self.off[:2] == 0)
            )),
            self.rdata.eq(0),
            Case(self.off, {
                0x00: self.rdata.eq(0xae35010a),
                0x04: self.rdata.eq(ctrl_word),
                0x08: self.rdata.eq(status_word),
                0x0C: self.rdata.eq(err_word),
                0x10: self.rdata.eq(rx_frames_sys),
                0x14: self.rdata.eq(tx_frames_sys),
                0x18: self.rdata.eq(rx_bytes_sys),
                0x1C: self.rdata.eq(tx_bytes_sys),
                0x20: self.rdata.eq(rx_errs_sys),
            })
        ]

        self.sync += [
            ctrl_clear_stats.eq(0),
            If(self.commit & self.ok & self.write & (self.off == 0x04),
                ctrl_tx_disable.eq(self.wdata[0]),
                ctrl_sig_detect_ovr.eq(self.wdata[1]),
                ctrl_loopback.eq(self.wdata[2]),
                ctrl_pkt_gen_arm.eq(self.wdata[3]),
                ctrl_clear_stats.eq(self.wdata[4]),
                ctrl_dma_en.eq(self.wdata[5]),
                If(self.wdata[16:32] != 0,
                    ctrl_pkt_gen_count.eq(self.wdata[16:32])
                )
            )
        ]
