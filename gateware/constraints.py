"""Specific exceptions for the pinned GW2DDRPHY initialization sequence.

GW2DDRPHYInit stops sys2x before asserting reset, releases reset while
sys2x is still stopped, then restarts clocks 8 init cycles later. These
reset paths are not synchronous transfers. Pause crosses a MultiReg;
only the first synchronizer stage is exempt. CPU/controller/DDR data paths
remain timed, including the second synchronizer stage.
"""
from pathlib import Path
import re


def add_ddr_init_exceptions(platform):
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
        return result

    platform.toolchain.build_timing_constraints = build
