"""Dual SFP Subsystem for Tang Mega 138K Pro.

Supports:
- SFP0: 10GBASE-R optical Ethernet via Gowin SerDes Quad 1 (Lane 0) + 10G MAC IP (156.25 MHz).
- SFP1: 1G BASE-X optical / SGMII copper Ethernet via Gowin SerDes Quad 1 (Lane 1) + 1G MAC IP (125 MHz).

Reference clock is external 125 MHz differential input from onboard MS5351.
Includes hardware line-rate frame generators, receiver monitors, APB registers,
and independent DMA bridges to LiteEth EthernetDMA.
"""
from pathlib import Path
from migen import *
from migen.genlib.cdc import MultiReg
from litex.gen import LiteXModule


def add_sfp_sources(platform):
    """Add encrypted vendor Verilog sources for SerDes, 10G MAC, and 1G PCS/MAC."""
    root = Path(__file__).resolve().parents[4]
    vendor_dir = root / 'gateware/vendor/gowin_gw5ast'
    sources = [
        vendor_dir / 'sfp_pll_156m/gowin_pll.v',
        vendor_dir / 'sfp_mac_10g/Ten_Giga_Ethernet_MAC.v',
        vendor_dir / 'sfp_serdes/serdes.v',
        vendor_dir / 'sfp_serdes/ten_giga_serial_ethernet/ten_giga_serial_ethernet.v',
        vendor_dir / 'sfp_ge_1g/ge_pcs.v',
        vendor_dir / 'sfp_ge_1g/ge_pcs_wrap.v',
    ]
    for src in sources:
        platform.add_source(str(src))
    platform.add_verilog_include_path(str(vendor_dir / 'sfp_serdes'))
    platform.add_verilog_include_path(str(vendor_dir / 'sfp_serdes/ten_giga_serial_ethernet'))
    platform.add_verilog_include_path(str(vendor_dir / 'sfp_ge_1g'))
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
    """Dual SFP controller (SFP0 10GBASE-R and SFP1 1G BASE-X/SGMII) with APB CSRs."""
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

        # Timing constraints for 10G XGMII, 1G fabric, and SerDes clocks
        self.constraints = [
            'create_clock -name xgmii_clk -period 6.400 -waveform {0 3.200} [get_pins {Xge_pll/PLL_inst/CLKOUT0}]',
            'create_clock -name ge_clk -period 8.000 -waveform {0 4.000} [get_pins {Xge_pll/PLL_inst/CLKOUT1}]',
            'create_clock -name serdes1_tx_clk -period 6.173 -waveform {0 3.087} [get_nets {u_sfp_serdes/q1_lane0_fabric_tx_clk}]',
            'create_clock -name serdes1_rx_clk -period 6.173 -waveform {0 3.087} [get_nets {u_sfp_serdes/q1_lane0_fabric_rx_clk}]',
            'create_clock -name serdes2_tx_clk -period 8.000 -waveform {0 4.000} [get_nets {sfp1_tx_mac_clk}]',
            'create_clock -name serdes2_rx_clk -period 8.000 -waveform {0 4.000} [get_nets {sfp1_rx_mac_clk}]',
        ]

        # ---------------------------------------------------------------------
        # Clock & Reset Generation
        # ---------------------------------------------------------------------
        serdes_refclk = Signal()
        xge_clk = Signal()
        ge_clk = Signal()
        xge_clk_lock = Signal()

        self.specials += Instance('Xge_pll',
            i_clkin=serdes_refclk,
            o_clkout0=xge_clk,
            o_clkout1=ge_clk,
            o_lock=xge_clk_lock,
        )

        serdes_rstn = Signal()
        self.comb += serdes_rstn.eq(xge_clk_lock)

        # ---------------------------------------------------------------------
        # SFP0 Controls & Signals (10GBASE-R)
        # ---------------------------------------------------------------------
        sfp0_los_synced = Signal()
        self.specials += MultiReg(sfp0_pads.los, sfp0_los_synced, 'sys')

        ctrl0_tx_disable = Signal(reset=0)
        ctrl0_sig_detect_ovr = Signal(reset=0)
        ctrl0_loopback = Signal(reset=0)
        ctrl0_pkt_gen_arm = Signal()
        ctrl0_pkt_gen_count = Signal(16, reset=10)
        ctrl0_clear_stats = Signal()
        ctrl0_dma_en = Signal(reset=1)

        sfp0_signal_detected = Signal()
        self.comb += [
            sfp0_signal_detected.eq(Mux(ctrl0_sig_detect_ovr, 1, ~sfp0_los_synced)),
            sfp0_pads.tx_disable.eq(ctrl0_tx_disable),
        ]

        # ---------------------------------------------------------------------
        # SFP1 Controls & Signals (1G BASE-X / SGMII)
        # ---------------------------------------------------------------------
        sfp1_los_synced = Signal()
        if sfp1_pads is not None:
            self.specials += MultiReg(sfp1_pads.los, sfp1_los_synced, 'sys')
        else:
            self.comb += sfp1_los_synced.eq(1)

        sfp1_ctrl_tx_disable = Signal(reset=0)
        sfp1_ctrl_sig_detect_ovr = Signal(reset=0)
        sfp1_ctrl_loopback = Signal(reset=0)
        sfp1_ctrl_pkt_gen_arm = Signal()
        sfp1_ctrl_pkt_gen_count = Signal(16, reset=10)
        sfp1_ctrl_clear_stats = Signal()
        sfp1_ctrl_dma_en = Signal(reset=0)
        sfp1_ctrl_basex_or_sgmii = Signal(reset=0) # 0: 1000BASE-X optical, 1: SGMII copper
        sfp1_ctrl_sgmii_mode = Signal(reset=0)     # 0: MAC mode, 1: PHY mode

        sfp1_signal_detected = Signal()
        self.comb += sfp1_signal_detected.eq(Mux(sfp1_ctrl_sig_detect_ovr, 1, ~sfp1_los_synced))
        if sfp1_pads is not None:
            self.comb += sfp1_pads.tx_disable.eq(sfp1_ctrl_tx_disable)

        # ---------------------------------------------------------------------
        # SerDes Instance (Quad 1: Lane 0 = 10G, Lane 1 = 1G)
        # ---------------------------------------------------------------------
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

        sfp1_rx_mac_clk = Signal(name_override="sfp1_rx_mac_clk")
        sfp1_rx_mac_valid = Signal()
        sfp1_rx_mac_data = Signal(8)
        sfp1_rx_mac_last = Signal()
        sfp1_rx_mac_error = Signal()

        sfp1_tx_mac_clk = Signal(name_override="sfp1_tx_mac_clk")
        sfp1_tx_mac_valid = Signal()
        sfp1_tx_mac_data = Signal(8)
        sfp1_tx_mac_last = Signal()
        sfp1_tx_mac_error = Signal()
        sfp1_tx_mac_ready = Signal()

        sfp1_status_vector = Signal(11)
        sfp1_link_partner_ability = Signal(16)

        self.specials += Instance('SerDes_Top',
            name='u_sfp_serdes',
            # Channel 0 (Lane 0 / SFP0 10GBASE-R)
            o_Ten_Giga_Serial_Ethernet_Top_ref_clk_o=serdes_refclk,
            o_Ten_Giga_Serial_Ethernet_Top_clk_out0_o=Signal(),
            o_Ten_Giga_Serial_Ethernet_Top_clk_out1_o=Signal(),
            i_Ten_Giga_Serial_Ethernet_Top_rx_rstn_i=serdes_rstn,
            i_Ten_Giga_Serial_Ethernet_Top_tx_rstn_i=serdes_rstn,
            i_Ten_Giga_Serial_Ethernet_Top_signal_detect_i=sfp0_signal_detected,
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
            i_Ten_Giga_Serial_Ethernet_Top_clear_ber_count_i=ctrl0_clear_stats,
            i_Ten_Giga_Serial_Ethernet_Top_clear_errored_block_count_i=ctrl0_clear_stats,
            # Channel 1 (Lane 1 / SFP1 1G BASE-X / SGMII)
            i_sfp1_clk_125m_i=ge_clk,
            i_sfp1_rstn_i=serdes_rstn,
            i_sfp1_signal_detect_i=sfp1_signal_detected,
            i_sfp1_basex_link_timer_i=305, # 10 ms at 32.768 us / tick
            i_sfp1_sgmii_link_timer_i=49,  # 1.6 ms at 32.768 us / tick
            i_sfp1_basex_or_sgmii_i=sfp1_ctrl_basex_or_sgmii,
            i_sfp1_sgmii_mode_i=sfp1_ctrl_sgmii_mode,
            o_sfp1_rx_mac_clk_o=sfp1_rx_mac_clk,
            o_sfp1_rx_mac_valid_o=sfp1_rx_mac_valid,
            o_sfp1_rx_mac_data_o=sfp1_rx_mac_data,
            o_sfp1_rx_mac_last_o=sfp1_rx_mac_last,
            o_sfp1_rx_mac_error_o=sfp1_rx_mac_error,
            o_sfp1_tx_mac_clk_o=sfp1_tx_mac_clk,
            i_sfp1_tx_mac_valid_i=sfp1_tx_mac_valid,
            i_sfp1_tx_mac_data_i=sfp1_tx_mac_data,
            i_sfp1_tx_mac_last_i=sfp1_tx_mac_last,
            i_sfp1_tx_mac_error_i=sfp1_tx_mac_error,
            o_sfp1_tx_mac_ready_o=sfp1_tx_mac_ready,
            o_sfp1_status_vector_o=sfp1_status_vector,
            o_sfp1_link_partner_ability_o=sfp1_link_partner_ability,
        )

        # ---------------------------------------------------------------------
        # SFP0 10G Ethernet MAC Instance
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
        # SFP0 Hardware Packet Generator & Statistics (10G domain)
        # ---------------------------------------------------------------------
        rx_frame_cnt = Signal(32)
        tx_frame_cnt = Signal(32)
        rx_byte_cnt = Signal(32)
        tx_byte_cnt = Signal(32)
        rx_err_cnt = Signal(32)

        pkt_gen_busy = Signal()
        pkt_gen_word_idx = Signal(8)
        pkt_gen_rem_pkts = Signal(16)
        pkt_gen_armed_sync = Signal()
        self.specials += MultiReg(ctrl0_pkt_gen_arm, pkt_gen_armed_sync, 'eth_sfp_tx')

        gen_tx_valid = Signal()
        gen_tx_data  = Signal(64)
        gen_tx_byte  = Signal(8)
        gen_tx_last  = Signal()

        self.clock_domains.cd_eth_sfp_tx = ClockDomain('eth_sfp_tx')
        self.clock_domains.cd_eth_sfp_rx = ClockDomain('eth_sfp_rx')
        self.comb += [
            self.cd_eth_sfp_tx.clk.eq(tx_mac_clk),
            self.cd_eth_sfp_tx.rst.eq(~mac_rstn),
            self.cd_eth_sfp_rx.clk.eq(rx_mac_clk),
            self.cd_eth_sfp_rx.rst.eq(~mac_rstn),
        ]

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

            If(ctrl0_clear_stats,
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
                        pkt_gen_rem_pkts.eq(ctrl0_pkt_gen_count),
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
        # SFP0 DMA Bridge (32-bit sys @ 50 MHz <-> 64-bit 10G MAC @ 156.25 MHz)
        # ---------------------------------------------------------------------
        from gateware.machines.mega138k_ae350.peripherals.sfp_bridge import SFP10GDMABridge
        self.submodules.bridge = bridge = SFP10GDMABridge(depth=512)
        self.dma_sink = bridge.dma_sink
        self.dma_source = bridge.dma_source

        self.comb += [
            bridge.rx_mac_valid.eq(rx_mac_valid),
            bridge.rx_mac_data.eq(rx_mac_data),
            bridge.rx_mac_byte.eq(rx_mac_byte),
            bridge.rx_mac_last.eq(rx_mac_last),
            bridge.rx_mac_error.eq(rx_mac_error),
        ]

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
            If(ctrl0_clear_stats,
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
        # SFP1 Hardware Packet Generator & Statistics (1G domain)
        # ---------------------------------------------------------------------
        self.clock_domains.cd_eth_sfp1_tx = ClockDomain('eth_sfp1_tx')
        self.clock_domains.cd_eth_sfp1_rx = ClockDomain('eth_sfp1_rx')
        self.comb += [
            self.cd_eth_sfp1_tx.clk.eq(sfp1_tx_mac_clk),
            self.cd_eth_sfp1_tx.rst.eq(~serdes_rstn),
            self.cd_eth_sfp1_rx.clk.eq(sfp1_rx_mac_clk),
            self.cd_eth_sfp1_rx.rst.eq(~serdes_rstn),
        ]

        sfp1_pkt_gen_busy = Signal()
        sfp1_pkt_gen_rem_pkts = Signal(16)
        sfp1_pkt_gen_byte_idx = Signal(8)
        sfp1_pkt_gen_armed_sync = Signal()
        self.specials += MultiReg(sfp1_ctrl_pkt_gen_arm, sfp1_pkt_gen_armed_sync, 'eth_sfp1_tx')

        sfp1_gen_valid = Signal()
        sfp1_gen_data = Signal(8)
        sfp1_gen_last = Signal()

        sfp1_test_bytes = [
            0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
            0x00, 0x0a, 0x35, 0x01, 0x02, 0x03,
            0x08, 0x00,
            0x45, 0x00, 0x00, 0x2e,
            0x00, 0x01, 0x00, 0x00,
            0x40, 0x11, 0x00, 0x00,
            0xc0, 0xa8, 0x0a, 0x02,
            0xc0, 0xa8, 0x0a, 0x01,
            0x1f, 0x40, 0x1f, 0x40,
            0x00, 0x1a, 0x00, 0x00,
            0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
            0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f, 0x10,
            0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18,
            0x19, 0x1a, 0x1b, 0x1c, 0x1d, 0x1e, 0x1f, 0x20,
        ]
        sfp1_byte_lut = Array([Constant(b, 8) for b in sfp1_test_bytes])

        sfp1_gen_armed_d = Signal()
        sfp1_gen_trigger = Signal()
        sfp1_gen_state = Signal(2, reset=GEN_IDLE)

        sfp1_rx_frame_cnt = Signal(32)
        sfp1_tx_frame_cnt = Signal(32)
        sfp1_rx_byte_cnt = Signal(32)
        sfp1_tx_byte_cnt = Signal(32)
        sfp1_rx_err_cnt = Signal(32)

        self.sync.eth_sfp1_tx += [
            sfp1_gen_armed_d.eq(sfp1_pkt_gen_armed_sync),
            sfp1_gen_trigger.eq(sfp1_pkt_gen_armed_sync & ~sfp1_gen_armed_d),

            If(sfp1_ctrl_clear_stats,
                sfp1_tx_frame_cnt.eq(0),
                sfp1_tx_byte_cnt.eq(0),
            ).Elif(sfp1_tx_mac_valid & sfp1_tx_mac_ready,
                sfp1_tx_byte_cnt.eq(sfp1_tx_byte_cnt + 1),
                If(sfp1_tx_mac_last,
                    sfp1_tx_frame_cnt.eq(sfp1_tx_frame_cnt + 1)
                )
            ),

            Case(sfp1_gen_state, {
                GEN_IDLE: [
                    sfp1_pkt_gen_busy.eq(0),
                    sfp1_gen_valid.eq(0),
                    sfp1_gen_last.eq(0),
                    If(sfp1_gen_trigger,
                        sfp1_pkt_gen_busy.eq(1),
                        sfp1_pkt_gen_rem_pkts.eq(sfp1_ctrl_pkt_gen_count),
                        sfp1_pkt_gen_byte_idx.eq(0),
                        sfp1_gen_valid.eq(1),
                        sfp1_gen_data.eq(sfp1_byte_lut[0]),
                        sfp1_gen_last.eq(0),
                        sfp1_gen_state.eq(GEN_SEND),
                    )
                ],
                GEN_SEND: [
                    sfp1_pkt_gen_busy.eq(1),
                    If(sfp1_tx_mac_ready,
                        If(sfp1_pkt_gen_byte_idx == len(sfp1_test_bytes) - 1,
                            sfp1_gen_valid.eq(0),
                            sfp1_gen_last.eq(0),
                            sfp1_pkt_gen_byte_idx.eq(0),
                            If(sfp1_pkt_gen_rem_pkts <= 1,
                                sfp1_pkt_gen_busy.eq(0),
                                sfp1_gen_state.eq(GEN_IDLE),
                            ).Else(
                                sfp1_pkt_gen_rem_pkts.eq(sfp1_pkt_gen_rem_pkts - 1),
                                sfp1_gen_state.eq(GEN_GAP),
                            )
                        ).Else(
                            sfp1_pkt_gen_byte_idx.eq(sfp1_pkt_gen_byte_idx + 1),
                            sfp1_gen_data.eq(sfp1_byte_lut[sfp1_pkt_gen_byte_idx + 1]),
                            If(sfp1_pkt_gen_byte_idx == len(sfp1_test_bytes) - 2,
                                sfp1_gen_last.eq(1)
                            ).Else(
                                sfp1_gen_last.eq(0)
                            )
                        )
                    )
                ],
                GEN_GAP: [
                    sfp1_pkt_gen_busy.eq(1),
                    sfp1_gen_valid.eq(0),
                    sfp1_gen_last.eq(0),
                    sfp1_pkt_gen_byte_idx.eq(sfp1_pkt_gen_byte_idx + 1),
                    If(sfp1_pkt_gen_byte_idx == 12,
                        sfp1_pkt_gen_byte_idx.eq(0),
                        sfp1_gen_valid.eq(1),
                        sfp1_gen_data.eq(sfp1_byte_lut[0]),
                        sfp1_gen_last.eq(0),
                        sfp1_gen_state.eq(GEN_SEND),
                    )
                ]
            })
        ]

        self.sync.eth_sfp1_rx += [
            If(sfp1_ctrl_clear_stats,
                sfp1_rx_frame_cnt.eq(0),
                sfp1_rx_byte_cnt.eq(0),
                sfp1_rx_err_cnt.eq(0),
            ).Elif(sfp1_rx_mac_valid,
                sfp1_rx_byte_cnt.eq(sfp1_rx_byte_cnt + 1),
                If(sfp1_rx_mac_error, sfp1_rx_err_cnt.eq(sfp1_rx_err_cnt + 1)),
                If(sfp1_rx_mac_last,
                    sfp1_rx_frame_cnt.eq(sfp1_rx_frame_cnt + 1)
                )
            )
        ]

        # ---------------------------------------------------------------------
        # SFP1 DMA Bridge (32-bit sys @ 50 MHz <-> 8-bit 1G MAC @ 125 MHz)
        # ---------------------------------------------------------------------
        from gateware.machines.mega138k_ae350.peripherals.sfp_bridge import SFP1GDMABridge
        self.submodules.sfp1_bridge = sfp1_bridge = SFP1GDMABridge(depth=512)
        self.sfp1_dma_sink = sfp1_bridge.dma_sink
        self.sfp1_dma_source = sfp1_bridge.dma_source

        self.comb += [
            sfp1_bridge.rx_mac_valid.eq(sfp1_rx_mac_valid),
            sfp1_bridge.rx_mac_data.eq(sfp1_rx_mac_data),
            sfp1_bridge.rx_mac_last.eq(sfp1_rx_mac_last),
            sfp1_bridge.rx_mac_error.eq(sfp1_rx_mac_error),
        ]

        self.comb += [
            If(sfp1_pkt_gen_busy,
                sfp1_tx_mac_valid.eq(sfp1_gen_valid),
                sfp1_tx_mac_data.eq(sfp1_gen_data),
                sfp1_tx_mac_last.eq(sfp1_gen_last),
                sfp1_tx_mac_error.eq(0),
                sfp1_bridge.tx_mac_ready.eq(0),
            ).Else(
                sfp1_tx_mac_valid.eq(sfp1_bridge.tx_mac_valid),
                sfp1_tx_mac_data.eq(sfp1_bridge.tx_mac_data),
                sfp1_tx_mac_last.eq(sfp1_bridge.tx_mac_last),
                sfp1_tx_mac_error.eq(sfp1_bridge.tx_mac_error),
                sfp1_bridge.tx_mac_ready.eq(sfp1_tx_mac_ready),
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

        sfp1_status_vector_sys = Signal(11)
        sfp1_link_partner_ability_sys = Signal(16)
        sfp1_frames_rx_sys = Signal(32)
        sfp1_frames_tx_sys = Signal(32)
        sfp1_bytes_rx_sys = Signal(32)
        sfp1_bytes_tx_sys = Signal(32)
        sfp1_errs_rx_sys = Signal(32)
        self.specials += [
            MultiReg(sfp1_status_vector, sfp1_status_vector_sys),
            MultiReg(sfp1_link_partner_ability, sfp1_link_partner_ability_sys),
            MultiReg(sfp1_rx_frame_cnt, sfp1_frames_rx_sys),
            MultiReg(sfp1_tx_frame_cnt, sfp1_frames_tx_sys),
            MultiReg(sfp1_rx_byte_cnt, sfp1_bytes_rx_sys),
            MultiReg(sfp1_tx_byte_cnt, sfp1_bytes_tx_sys),
            MultiReg(sfp1_rx_err_cnt, sfp1_errs_rx_sys),
        ]

        self.sfp1_link_status_sys = sfp1_status_vector_sys[0]
        self.sfp1_los_synced = sfp1_los_synced

        # ---------------------------------------------------------------------
        # DMA Routing Selection (0: RGMII, 1: SFP0 10G, 2: SFP1 1G)
        # ---------------------------------------------------------------------
        self.dma_sel = Signal(2)
        self.dma_en = Signal()
        self.comb += [
            If(sfp1_ctrl_dma_en,
                self.dma_sel.eq(2)
            ).Elif(ctrl0_dma_en,
                self.dma_sel.eq(1)
            ).Else(
                self.dma_sel.eq(0)
            ),
            self.dma_en.eq(self.dma_sel != 0),
        ]

        # ---------------------------------------------------------------------
        # APB Target Registers (System Domain)
        # ---------------------------------------------------------------------
        # SFP0 10G Words
        status0_word = Signal(32)
        self.comb += status0_word.eq(Cat(
            xge_clk_lock,       # bit 0
            block_lock_sys,     # bit 1
            pcs_status_sys,     # bit 2
            sfp0_los_synced,    # bit 3
            hi_ber_sys,         # bit 4
            local_fault_sys,    # bit 5
            remote_fault_sys,   # bit 6
            pkt_gen_busy,       # bit 7
            ctrl0_dma_en,       # bit 8
            Constant(0, 23)
        ))

        err0_word = Signal(32)
        self.comb += err0_word.eq(Cat(ber_count, Constant(0, 2), errored_block_count, Constant(0, 17)))

        ctrl0_word = Signal(32)
        self.comb += ctrl0_word.eq(Cat(
            ctrl0_tx_disable,     # bit 0
            ctrl0_sig_detect_ovr, # bit 1
            ctrl0_loopback,       # bit 2
            ctrl0_pkt_gen_arm,    # bit 3
            ctrl0_clear_stats,    # bit 4
            ctrl0_dma_en,         # bit 5
            Constant(0, 10),
            ctrl0_pkt_gen_count,  # bits 16..31
        ))

        # SFP1 1G Words
        status1_word = Signal(32)
        self.comb += status1_word.eq(Cat(
            xge_clk_lock,                   # bit 0
            sfp1_status_vector_sys[0],      # bit 1: link_status
            sfp1_status_vector_sys[1],      # bit 2: an_complete
            sfp1_los_synced,                # bit 3: sfp_los
            sfp1_status_vector_sys[2],      # bit 4: rx_sync
            sfp1_status_vector_sys[3],      # bit 5: remote_fault
            sfp1_status_vector_sys[4],      # bit 6: error_detected
            sfp1_pkt_gen_busy,              # bit 7: pkt_gen_busy
            sfp1_ctrl_dma_en,               # bit 8: dma_en status
            sfp1_ctrl_basex_or_sgmii,       # bit 9: 0=BASE-X, 1=SGMII
            Constant(0, 6),
            sfp1_status_vector_sys,         # bits 16..26
            Constant(0, 5)
        ))

        ctrl1_word = Signal(32)
        self.comb += ctrl1_word.eq(Cat(
            sfp1_ctrl_tx_disable,     # bit 0
            sfp1_ctrl_sig_detect_ovr, # bit 1
            sfp1_ctrl_loopback,       # bit 2
            sfp1_ctrl_pkt_gen_arm,    # bit 3
            sfp1_ctrl_clear_stats,    # bit 4
            sfp1_ctrl_dma_en,         # bit 5
            sfp1_ctrl_basex_or_sgmii, # bit 6
            sfp1_ctrl_sgmii_mode,     # bit 7
            Constant(0, 8),
            sfp1_ctrl_pkt_gen_count,  # bits 16..31
        ))

        self.comb += [
            self.ok.eq(self.selected & Mux(self.write,
                (self.off == 0x04) | (self.off == 0x44),
                ((self.off <= 0x24) | ((self.off >= 0x40) & (self.off <= 0x64))) & (self.off[:2] == 0)
            )),
            self.rdata.eq(0),
            Case(self.off, {
                # SFP0 10G
                0x00: self.rdata.eq(0xae35010a),
                0x04: self.rdata.eq(ctrl0_word),
                0x08: self.rdata.eq(status0_word),
                0x0C: self.rdata.eq(err0_word),
                0x10: self.rdata.eq(rx_frames_sys),
                0x14: self.rdata.eq(tx_frames_sys),
                0x18: self.rdata.eq(rx_bytes_sys),
                0x1C: self.rdata.eq(tx_bytes_sys),
                0x20: self.rdata.eq(rx_errs_sys),
                # SFP1 1G
                0x40: self.rdata.eq(0xae350001),
                0x44: self.rdata.eq(ctrl1_word),
                0x48: self.rdata.eq(status1_word),
                0x4C: self.rdata.eq(sfp1_link_partner_ability_sys),
                0x50: self.rdata.eq(sfp1_frames_rx_sys),
                0x54: self.rdata.eq(sfp1_frames_tx_sys),
                0x58: self.rdata.eq(sfp1_bytes_rx_sys),
                0x5C: self.rdata.eq(sfp1_bytes_tx_sys),
                0x60: self.rdata.eq(sfp1_errs_rx_sys),
            })
        ]

        self.sync += [
            ctrl0_clear_stats.eq(0),
            sfp1_ctrl_clear_stats.eq(0),
            If(self.commit & self.ok & self.write,
                If(self.off == 0x04,
                    ctrl0_tx_disable.eq(self.wdata[0]),
                    ctrl0_sig_detect_ovr.eq(self.wdata[1]),
                    ctrl0_loopback.eq(self.wdata[2]),
                    ctrl0_pkt_gen_arm.eq(self.wdata[3]),
                    ctrl0_clear_stats.eq(self.wdata[4]),
                    ctrl0_dma_en.eq(self.wdata[5]),
                    If(self.wdata[16:32] != 0,
                        ctrl0_pkt_gen_count.eq(self.wdata[16:32])
                    )
                ).Elif(self.off == 0x44,
                    sfp1_ctrl_tx_disable.eq(self.wdata[0]),
                    sfp1_ctrl_sig_detect_ovr.eq(self.wdata[1]),
                    sfp1_ctrl_loopback.eq(self.wdata[2]),
                    sfp1_ctrl_pkt_gen_arm.eq(self.wdata[3]),
                    sfp1_ctrl_clear_stats.eq(self.wdata[4]),
                    sfp1_ctrl_dma_en.eq(self.wdata[5]),
                    sfp1_ctrl_basex_or_sgmii.eq(self.wdata[6]),
                    sfp1_ctrl_sgmii_mode.eq(self.wdata[7]),
                    If(self.wdata[16:32] != 0,
                        sfp1_ctrl_pkt_gen_count.eq(self.wdata[16:32])
                    )
                )
            )
        ]
