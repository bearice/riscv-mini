"""Small LiteX GowinPlatform factory and electrical-value translation."""
from litex.build.gowin.platform import GowinPlatform



def create_platform(bindings, clock_name, options):
    """Instantiate LiteX directly from translated JSON resources."""
    board = bindings.board
    platform = GowinPlatform(board.fpga.part, bindings.resources, devicename=board.fpga.device)
    platform.default_clk_name = clock_name
    platform.default_clk_period = 1e9 / board.input_clock_hz
    platform.toolchain.options.update(options)
    return platform


def constraints(signal, index=None):
    """Convert electrical configuration to LiteX constraints, including per-bit drive."""
    from litex.build.generic_platform import IOStandard, Misc
    result = [IOStandard(signal.io_standard)]
    values = []
    if signal.bank_vccio is not None:
        values.append('BANK_VCCIO=%s' % signal.bank_vccio)
    if signal.vref is not None:
        values.append('VREF=' + signal.vref.upper())
    if signal.pull is not None:
        values.append('PULL_MODE=' + signal.pull.upper())
    drive = (signal.drive_overrides or {}).get(str(index), signal.drive_ma)
    if drive is not None:
        values.append('DRIVE=%d' % drive)
    if values:
        result.append(Misc(' '.join(values)))
    return result
