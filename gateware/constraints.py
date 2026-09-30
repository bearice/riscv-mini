"""Specific exceptions for the pinned GW2DDRPHY initialization sequence.

GW2DDRPHYInit stops sys2x before asserting reset, releases reset while
sys2x is still stopped, then restarts clocks 8 init cycles later. These
reset paths are not synchronous transfers. Pause crosses a MultiReg;
only the first synchronizer stage is exempt. CPU/controller/DDR data paths
remain timed, including the second synchronizer stage.
"""
from pathlib import Path
import re


def add_ddr_init_exceptions(platform, with_video=False):
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
            stream.write(f'set_false_path -from [get_pins {{ddr_init_pause_s0/Q}}] -to [get_pins {{{stages[0]}_s0/D}}]\n')
            if with_video:
                pairs=re.findall(r'\b(\w+)\s*<=\s*(\w+)\s*;',rtl)
                stages=[dest for dest,src in pairs if src in ('lcd_video_enable','lcd_video_test') or
                    re.search(r'(?:cdc_cdc_graycounter[01]_q|(?:start|done|error)_toggle_i)$',src)]
                if len(stages)!=7: raise ValueError(f'Unexpected LCD CDC first stages: {stages}')
                stream.write('# LCD CDC: only async first-stage D pins; second stages stay timed.\n')
                stream.write('set_false_path -through [get_nets {lcd_video_async_reset}]\n')
                declarations={name:int(msb)+1 for msb,name in re.findall(r'reg\s+\[(\d+):0\]\s+(\w+)',rtl)}
                for name in stages:
                    width=declarations.get(name,1)
                    targets=[f'{name}_{i}_s0/D' for i in range(width)] if width>1 else [f'{name}_s0/D']
                    for target in targets: stream.write(f'set_false_path -to [get_pins {{{target}}}]\n')
        return result

    platform.toolchain.build_timing_constraints = build
