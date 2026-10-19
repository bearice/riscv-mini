"""Resolve the live Gowin USB Debugger location before programming.

Only the USB *location* identifies a cable uniquely; the position of a line in
the scan output is a list index, not a channel number, and it changes when a
second debugger is plugged in.  Nothing here may be used as ``--cable-index``.
"""
import re
import subprocess


def _scan(programmer, *extra, runner=None):
    command = [str(programmer), *extra]
    result = (runner or subprocess.run)(command, capture_output=True, text=True, timeout=15)
    return result.returncode, (result.stdout or '') + (result.stderr or '')


def _matches(output, identity):
    if identity is None:
        return '1 device(s) found' in output
    if isinstance(identity, int):
        pattern = r'ID:\s*0x' + ('%08x' % identity)
        return bool(re.search(pattern, output, re.I)) and '1 device(s) found' in output
    return bool(re.search(identity, output)) and '1 device(s) found' in output


def resolve_location(programmer, location=None, *, identity=None, runner=None, log_path=None):
    if location is not None and str(location).lower() != 'auto':
        # An explicit location is still verified against the expected target
        # before any programming happens.
        if identity is not None:
            rc, out = _scan(programmer, '--location', str(location), '--scan', runner=runner)
            if rc or not _matches(out, identity):
                raise RuntimeError('Location %s does not report the expected target identity' % location)
        return str(location)
    rc, output = _scan(programmer, '--scan-cables', 'F', runner=runner)
    if log_path is not None:
        log_path.write_text(output, encoding='utf-8')
    if rc:
        raise RuntimeError('Gowin cable scan failed: ' + output.strip())
    # Several interfaces/debuggers can be present; only the one whose scan
    # finds a GW5AS target is the FPGA cable.  Probing is bounded because an
    # interface that is not a JTAG cable makes the tool wait.
    candidates = sorted({int(loc) for _ch, loc in re.findall(r'/(\d+)/(\d+)/', output)
                         if int(loc) > 0})
    if not candidates:
        raise RuntimeError('No Gowin JTAG cable found; check --scan-cables F')
    if len(candidates) == 1 and identity is None:
        # One advertised interface: no probing needed (the 20K flow validates the
        # target through the boot protocol, the AE350 flow passes an identity).
        print('Detected Gowin JTAG location:', candidates[0], flush=True)
        return str(candidates[0])
    matched = []
    for loc in candidates:
        try:
            rc, out = _scan(programmer, '--location', str(loc), '--scan', runner=runner)
        except subprocess.TimeoutExpired:
            continue
        if not rc and _matches(out, identity):
            matched.append(loc)
    if len(matched) == 1:
        print('Detected Gowin JTAG location:', matched[0], flush=True)
        return str(matched[0])
    if len(matched) > 1:
        raise RuntimeError('Multiple matching FPGA targets: %r; specify --location explicitly' % matched)
    raise RuntimeError('None of the connected debuggers scans an FPGA target; found ' +
                       repr(candidates) + '. Specify --location explicitly.')
