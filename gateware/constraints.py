"""Specific exceptions for the pinned GW2DDRPHY initialization sequence.

GW2DDRPHYInit stops sys2x before asserting reset, releases reset while
sys2x is still stopped, then restarts clocks 8 init cycles later. These
reset paths are not synchronous transfers. Pause crosses a MultiReg;
only the first synchronizer stage is exempt. CPU/controller/DDR data paths
remain timed, including the second synchronizer stage.
"""
from pathlib import Path
import re
from migen.genlib.cdc import MultiReg, MultiRegImpl


class GowinMultiReg:
    @staticmethod
    def lower(special):
        impl=MultiRegImpl(special.i,special.o,special.odomain,special.n,special.reset)
        # Gowin otherwise packs wide two-stage synchronizers into LUT RAM.
        # They must remain separate flip-flops, including the Gray FIFO buses.
        for reg in impl.regs:
            reg.attr.add(('syn_preserve',1))
            reg.attr.add(('syn_keep',1))
        return impl


def add_ddr_init_exceptions(platform, with_video=False):
    original_verilog=platform.get_verilog
    def verilog(*args, special_overrides=None, **kwargs):
        overrides=dict(special_overrides or {});overrides[MultiReg]=GowinMultiReg
        return original_verilog(*args,special_overrides=overrides,**kwargs)
    platform.get_verilog=verilog
    original = platform.toolchain.build_timing_constraints

    def build(vns):
        result = original(vns)
        rtl=Path(f'{platform.toolchain._build_name}.v').read_text()
        aliases={'ddr_init_pause'}
        for _ in range(8):
            found={dest for dest,source in re.findall(r'assign\s+(\w+)\s*=\s*(\w+)\s*;',rtl) if source in aliases}
            if found<=aliases: break
            aliases|=found
        stages=[dest for dest,source in re.findall(r'\b(\w+)\s*<=\s*(\w+)\s*;',rtl) if source in aliases]
        if len(stages)!=1:
            raise ValueError(f'Cannot identify DDR pause first synchronizer: {stages}')
        with Path(result[0]).open('a', encoding='utf-8') as stream:
            stream.write('\n# GW2DDRPHY init reset is released with sys2x stopped.\n')
            stream.write('set_false_path -from [get_pins {ddr_init_reset_s0/Q}]\n')
            stream.write('set_false_path -from [get_pins {ddr_init_stop_s0/Q}] -to [get_pins {DHCEN/CE}]\n')
            stream.write(f'set_false_path -from [get_pins {{ddr_init_pause_s0/Q}}] -to [get_pins {{{stages[0]}_s1/D}}]\n')
            if with_video:
                pairs=re.findall(r'\b(\w+)\s*<=\s*(\w+)\s*;',rtl)
                stages=[dest for dest,src in pairs if src=='lcd_video_enable' or
                    re.fullmatch(r'rgb_lcd_(?:cdc_cdc_graycounter[01]_q|(?:start|done|error)_toggle_i)',src)]
                if len(stages)!=6: raise ValueError(f'Unexpected LCD CDC first stages: {stages}')
                stream.write('# LCD CDC: only async first-stage D pins; second stages stay timed.\n')
                stream.write('set_false_path -through [get_nets {lcd_video_async_reset}]\n')
                declarations={name:int(msb)+1 for msb,name in re.findall(r'reg\s+\[(\d+):0\]\s+(\w+)',rtl)}
                for name in stages:
                    width=declarations.get(name,1)
                    targets=[f'{name}_{i}_s1/D' for i in range(width)] if width>1 else [f'{name}_s1/D']
                    for target in targets: stream.write(f'set_false_path -to [get_pins {{{target}}}]\n')
            # PHY MDIO input, free-running reference counter, MAC FIFO Gray
            # pointers and CRC/preamble pulse synchronizers cross async domains.
            pairs=re.findall(r'\b(\w+)\s*<=\s*(\w+)\s*;',rtl)
            usb_stages=[dest for dest,src in pairs if re.fullmatch(
                r'usb_(?:ulpi_(?:ready|error|id)|serial_lines)',src)]
            if usb_stages:
                if len(usb_stages)!=4:raise ValueError(f'Unexpected USB PHY CDC stages: {usb_stages}')
                declarations={name:int(msb)+1 for msb,name in re.findall(r'reg\s+\[(\d+):0\]\s+(\w+)',rtl)}
                stream.write('# USB PHY status: first stage only; ID is stable before ready.\n')
                for name in usb_stages:
                    width=declarations.get(name,1)
                    targets=[f'{name}_{i}_s1/D' for i in range(width)] if width>1 else [f'{name}_s1/D']
                    for target in targets:stream.write(f'set_false_path -to [get_pins {{{target}}}]\n')
                stream.write('# Pinned Spinal BufferCC CDC, all later stages remain timed.\n')
                stream.write('set_false_path -to [get_pins {UsbOhciWishbone_Dw32_Pc1_Pf48000000/*/buffers_0*_s1/D}]\n')
                stream.write('set_false_path -through [get_nets {usb_control_reset}]\n')
                resets=[name for name,body in re.findall(r'\bDFFP\s+(\w+)\s*\((.*?)\);',rtl,re.S)
                    if re.search(r'\.CLK\s*\((?:ulpi_clk|usb_ulpi_clk|usb_clk)\)',body)]
                if len(resets)!=4:raise ValueError(f'Unexpected USB reset synchronizers: {resets}')
                for name in resets:stream.write(f'set_false_path -to [get_pins {{{name}/PRESET}}]\n')
                # Gowin implements part of this first-stage async data input
                # as RESET. It is still the first CDC capture, not stage two.
                stream.write('set_false_path -to [get_pins {UsbOhciWishbone_Dw32_Pc1_Pf48000000/cc/input_lowSpeed_buffercc/buffers_0_s1/RESET}]\n')
                # TX payload is held until a three-stage synchronized request
                # and returned acknowledgement; one 48MHz period is sufficient.
                stream.write('set_max_delay 20 -from [get_pins {UsbOhciWishbone_Dw32_Pc1_Pf48000000/cc/input_tx_ccToggle/pushArea_data*_s0/Q}] -to [get_pins {UsbOhciWishbone_Dw32_Pc1_Pf48000000/cc/input_tx_ccToggle/popArea_stream_m2sPipe_rData*_s0/D}]\n')
                stream.write('set_false_path -hold -from [get_pins {UsbOhciWishbone_Dw32_Pc1_Pf48000000/cc/input_tx_ccToggle/pushArea_data*_s0/Q}] -to [get_pins {UsbOhciWishbone_Dw32_Pc1_Pf48000000/cc/input_tx_ccToggle/popArea_stream_m2sPipe_rData*_s0/D}]\n')
                # RX FlowCC holds each byte until the next full-speed byte
                # (>600ns). A synchronized toggle marks a later capture valid;
                # early samples in this register have no consumer.
                stream.write('set_max_delay 16 -from [get_pins {UsbOhciWishbone_Dw32_Pc1_Pf48000000/cc/output_rx_flow_ccToggle/inputArea_data*_s*/Q}] -to [get_pins {UsbOhciWishbone_Dw32_Pc1_Pf48000000/cc/output_rx_flow_ccToggle/outputArea_flow_regNext_payload*_s0/D}]\n')
                stream.write('set_false_path -hold -from [get_pins {UsbOhciWishbone_Dw32_Pc1_Pf48000000/cc/output_rx_flow_ccToggle/inputArea_data*_s*/Q}] -to [get_pins {UsbOhciWishbone_Dw32_Pc1_Pf48000000/cc/output_rx_flow_ccToggle/outputArea_flow_regNext_payload*_s0/D}]\n')
                serial_stages=[dest for dest,src in pairs if re.fullmatch(r'usb_serial_(?:dp|dm)_raw',src)]
                if len(serial_stages)!=2:raise ValueError(f'Unexpected serial RX synchronizers: {serial_stages}')
                for name in serial_stages:stream.write(f'set_false_path -to [get_pins {{{name}_s1/D}}]\n')
                stream.write('# USB3317 ULPI: tCO 1.5..3.5ns, tSU 5ns/tH 0ns, 0.5ns board budget.\n')
                stream.write('set_input_delay -max 4 -clock [get_clocks {usb_ulpi_clk}] [get_ports {usb_ulpi_dir usb_ulpi_nxt usb_ulpi_data[*]}]\n')
                stream.write('set_input_delay -min 1 -clock [get_clocks {usb_ulpi_clk}] [get_ports {usb_ulpi_dir usb_ulpi_nxt usb_ulpi_data[*]}]\n')
                stream.write('set_output_delay -max 5.5 -clock [get_clocks {usb_ulpi_clk}] [get_ports {usb_ulpi_stp usb_ulpi_data[*]}]\n')
                stream.write('set_output_delay -min -0.5 -clock [get_clocks {usb_ulpi_clk}] [get_ports {usb_ulpi_stp usb_ulpi_data[*]}]\n')
                # PLL P nominal edge +225deg + ~6.9ns insertion wraps into
                # the next external cycle (~0.7ns after PHY rising). The link
                # consumes the preceding falling sample and launches for the
                # FOLLOWING PHY rising edge. Account for that cycle label;
                # retain the intervening edge's hold check.
                # Address the output launch FFs directly: auto-generated PLL
                # clocks don't exist yet when this Gowin version parses SDC.
                stream.write('set_multicycle_path 2 -setup -from [get_pins {usb_host*_s*/Q usbhost_state*_s*/Q usb_ulpi_ready_s1/Q}] -to [get_ports {usb_ulpi_stp usb_ulpi_data[*]}]\n')
                # Serial TX is asynchronous to PHY's ULPI clock and operates
                # at 12Mbit/s. Bound its pad path, without an ULPI hold edge.
                # Gowin includes launch insertion and the 5.5ns output budget
                # in this limit as well; it is not a 25ns USB bit period.
                stream.write('set_max_delay 25 -from [get_pins {UsbOhciWishbone_Dw32_Pc1_Pf48000000/back_buffer_0_*write*_regNext*_s*/Q}] -to [get_ports {usb_ulpi_data[*]}]\n')
                stream.write('set_false_path -hold -from [get_pins {UsbOhciWishbone_Dw32_Pc1_Pf48000000/back_buffer_0_*write*_regNext*_s*/Q}] -to [get_ports {usb_ulpi_data[*]}]\n')
            eth_stages=[dest for dest,src in pairs if re.fullmatch(
                r'(?:eth_ref_gray|eth_mdio_in|'
                r'minisoc_core_(?:rx|tx)_cdc_cdc_graycounter[01]_q|'
                r'minisoc_core_pulsesynchronizer[01]_toggle_i)',src)]
            if eth_stages:
                if len(eth_stages)!=8: raise ValueError(f'Unexpected Ethernet CDC stages: {eth_stages}')
                stream.write('# Ethernet CDC: only first synchronizer stages.\n')
                declarations={name:int(msb)+1 for msb,name in re.findall(r'reg\s+\[(\d+):0\]\s+(\w+)',rtl)}
                for name in eth_stages:
                    width=declarations.get(name,1)
                    targets=[f'{name}_{i}_s1/D' for i in range(width)] if width>1 else [f'{name}_s1/D']
                    for target in targets: stream.write(f'set_false_path -to [get_pins {{{target}}}]\n')
                # Async assertions feed PRESET; release is synchronized by
                # the two DFFPs. Keep the synchronizer D paths timed.
                resets=[name for name,body in re.findall(r'\bDFFP\s+(\w+)\s*\((.*?)\);',rtl,re.S)
                    if re.search(r'\.CLK\s*\(eth_(?:(?:rx|tx|ref)_clk|clocks_ref_clk)\)',body)]
                if len(resets)!=6:raise ValueError(f'Unexpected Ethernet reset synchronizers: {resets}')
                for name in resets:stream.write(f'set_false_path -to [get_pins {{{name}/PRESET}}]\n')
                stream.write('# RTL8201F TX setup 4ns/hold 2ns, plus 0.5ns board budget.\n')
                stream.write('set_output_delay -max 4.5 -clock [get_clocks {eth_clocks_ref_clk}] [get_ports {rmii_tx_data[*] rmii_tx_en}]\n')
                stream.write('set_output_delay -min -2.5 -clock [get_clocks {eth_clocks_ref_clk}] [get_ports {rmii_tx_data[*] rmii_tx_en}]\n')
                stream.write('# RX engineering budget: 2ns minimum output delay, max 10ns incl. board.\n')
                stream.write('set_input_delay -max 10 -clock [get_clocks {eth_clocks_ref_clk}] [get_ports {rmii_rx_data[*] rmii_crs_dv rmii_rx_er}]\n')
                stream.write('set_input_delay -min 1.5 -clock [get_clocks {eth_clocks_ref_clk}] [get_ports {rmii_rx_data[*] rmii_crs_dv rmii_rx_er}]\n')
        return result

    platform.toolchain.build_timing_constraints = build
