"""Resolve the live Gowin USB Debugger A location before programming."""
import re
import subprocess


def resolve_location(programmer, location=None, *, runner=None, log_path=None):
    if location is not None and str(location).lower() != 'auto':
        return str(location)
    command = [str(programmer), '--scan-cables', 'F']
    result = (runner or subprocess.run)(command, capture_output=True, text=True, timeout=15)
    output = (result.stdout or '') + (result.stderr or '')
    if log_path is not None:
        log_path.write_text(output, encoding='utf-8')
    if result.returncode:
        raise RuntimeError('Gowin cable scan failed: ' + output.strip())
    # Channel B is the UART, often reported with location 0. Only select JTAG A.
    locations = sorted({loc for channel, loc in re.findall(
        r'Cable found:\s*USB Debugger A/(\d+)/(\d+)/', output)
        if channel == '0' and int(loc) > 0})
    if len(locations) != 1:
        raise RuntimeError('Expected one USB Debugger A JTAG cable; found ' +
                           repr(locations) + '. Check --scan-cables F or specify --location.')
    print('Detected Gowin JTAG location:', locations[0], flush=True)
    return locations[0]
