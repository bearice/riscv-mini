"""Canonical UART state protocol shared by firmware banners and host scripts.

Every firmware stage (bootloader, BIOS, monitor, DDR utilities) emits one
machine-readable line:

    STATE=<name> stage=<stage> abi=<8 hex> id=<build id>

`stage` is the emitting image, `abi` the image-ABI tag it enforces, `id` its
build identity. Host code must use this module instead of matching ad-hoc
banner substrings: `observe()` classifies the byte stream, `wait_for()` fails
immediately on a FAILED transition (any `ERR ` line) instead of waiting out a
timeout, and `require()` asserts an exact expected state.

Legacy builds without STATE= lines still classify through the fallback
patterns below, so the host keeps working against older images.
"""
import re
from enum import Enum


class State(Enum):
    BOOTING = 'booting'      # banner seen, boot not finished
    LOADER = 'loader'        # bootloader menu (BL>)
    APP_READY = 'app_ready'  # application/BIOS ready prompt
    FAILED = 'failed'        # an ERR line ended the attempt
    UNKNOWN = 'unknown'


# Ordered: the most specific ready prompt first. Each entry is (pattern, state).
FALLBACK = [
    (re.compile(rb'SYSTEM READY - FLASH READBACK'), State.APP_READY),
    (re.compile(rb'SYSTEM READY - BIOS'), State.APP_READY),
    (re.compile(rb'SYSTEM READY sd=\d+ spi_lcd=\d+ rgb_lcd=\d+'), State.APP_READY),
    (re.compile(rb'SYSTEM READY'), State.APP_READY),
    (re.compile(rb'BL> '), State.LOADER),
    (re.compile(rb'BOOT SELECT:'), State.BOOTING),
    (re.compile(rb'riscv-mini (?:ROM|XIP) '), State.BOOTING),
]

# Applications this system can boot that never print a STATE= line: U-Boot
# (via OpenSBI from BIOS) and the OpenSBI banner itself. Their prompts are
# classified APP_READY so the host never mistakes a live console for silence.
FOREIGN = [
    (re.compile(rb'u-boot>\s'), State.APP_READY),
    (re.compile(rb'u-boot#'), State.APP_READY),
    (re.compile(rb'U-Boot 20\d\d\.'), State.APP_READY),
    (re.compile(rb'Platform Name:\s+riscv-mini'), State.APP_READY),
    # A bare prompt with no banner: the application printed its ready line
    # long ago and it already drained from the FIFO. A newline probe that
    # echoes exactly a prompt back is liveness evidence for any console.
    (re.compile(rb'^\s*> $'), State.APP_READY),
    (re.compile(rb'^\s*BL> $'), State.LOADER),
]

STATE_LINE = re.compile(
    rb'STATE=(?P<state>[a-z_]+) stage=(?P<stage>\S+) abi=(?P<abi>[0-9a-f]{8}) id=(?P<id>\S+)')
ERROR_LINE = re.compile(rb'ERR [^\r\n]*')


def parse(data):
    """Classify a byte stream. Returns (State, detail dict or None).

    The last STATE= line wins; otherwise the last matching fallback pattern
    wins. A STATE= line never loses to a fallback pattern.
    """
    match = None
    for match in STATE_LINE.finditer(data):
        pass
    if match:
        name = match['state'].decode()
        detail = dict(stage=match['stage'].decode(), abi=match['abi'].decode(),
                      id=match['id'].decode(), state=name)
        try:
            return State(name), detail
        except ValueError:
            return State.UNKNOWN, detail
    for pattern, state in FALLBACK:
        if pattern.search(data):
            return state, None
    for pattern, state in FOREIGN:
        if pattern.search(data):
            return state, None
    return State.UNKNOWN, None


def observe(data):
    """Classify with failure precedence: any ERR line after the last state
    marker means FAILED, whatever banner preceded it."""
    state, detail = parse(data)
    # Only an ERR line after the last STATE= line fails the attempt; banners
    # that legitimately mention ERR (echoed text, readback payloads) sit
    # before the state marker and are ignored.
    marker = None
    for marker in STATE_LINE.finditer(data):
        pass
    tail = data[marker.end():] if marker else data
    if marker is None:
        # Legacy images have no STATE= line: an ERR line only fails once a
        # banner or prompt has been seen, so noise before boot is ignored.
        tail = tail if state is not State.UNKNOWN else b''
    error = ERROR_LINE.search(tail)
    if error:
        return State.FAILED, dict(error=error.group().decode(errors='replace'),
                                  previous=state.value)
    return state, detail


def require(data, expected, abi=None):
    """Assert the stream reached exactly `expected`; optionally the ABI tag.
    Returns the detail dict. Raises AssertionError with the raw tail."""
    state, detail = observe(data)
    if state is not expected:
        raise AssertionError(f'state {state.value} != expected {expected.value}; tail={bytes(data[-400:])!r}')
    if abi is not None and detail and detail.get('abi') != f'{abi:08x}':
        raise AssertionError(f"abi {detail['abi']} != expected {abi:08x}")
    return detail


def wait_for(port, expected, timeout=120, abi=None, log=None):
    """Read from `port` until the stream reaches `expected`.

    Fails immediately (RuntimeError) when the stream classifies as FAILED, so
    a broken boot never burns the whole timeout. Returns (data, detail).
    """
    import time
    data = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chunk = port.read(64)
        if chunk:
            data.extend(chunk)
            if log:
                log.write(chunk); log.flush()
            state, detail = observe(data)
            if state is State.FAILED:
                raise RuntimeError(f'board reported failure: {detail["error"]}; tail={bytes(data[-400:])!r}')
            if state is expected:
                if abi is not None and detail and detail.get('abi') != f'{abi:08x}':
                    raise AssertionError(f"abi {detail['abi']} != expected {abi:08x}")
                return bytes(data), detail
    raise TimeoutError(f'waiting for {expected.value}; tail={bytes(data[-400:])!r}')


class BoardFSM:
    """Authoritative board state with an explicit recovery ladder.

    The board is UNKNOWN until evidence says otherwise. Transitions:

        UNKNOWN --observe--> BOOTING | LOADER | APP_READY | FAILED
        FAILED  --probe----> LOADER (the loader menu survives app failures)
        any     --escalate-> LOADER | APP_READY | UNKNOWN

    Recovery escalates one step per attempt and never repeats a step that
    already produced UNKNOWN:

        1. query   - send a newline; a live loader answers 'BL> ',
                     a live application answers its prompt.
        2. reset   - '!' reboots the CPU; the fresh banner classifies BOOTING,
                     then the boot result classifies LOADER or APP_READY.
        3. sram    - program the gateware to SRAM (needs a bitstream); the
                     fresh banner proves the FPGA side is alive.
        4. UNKNOWN - nothing observed; the caller decides (reprogram Flash,
                     inspect hardware).

    `observe` never sends anything; every other step is logged through
    `note` so the operation receipt shows the ladder that was walked.
    """

    # Characters a console consumes as a command. A newline on an idle
    # BIOS/monitor prompt echoes one prompt back; on an idle loader menu it
    # echoes nothing. A busy application (U-Boot autoboot, a running demo)
    # swallows it or answers with its own output, which is why a single
    # newline is a safe, non-destructive liveness probe for any console.
    PROBE = b'\r'
    # Loader menu: 'b' enters the menu, '!' reboots the CPU. Both are
    # single-character commands the bootloader consumes immediately.
    MENU_KEY = b'b'
    RESET_KEY = b'!'

    def __init__(self, port, log=None, abi=None):
        self.port = port
        self.log = log
        self.abi = abi
        self.state = State.UNKNOWN
        self.detail = None
        self.history = []
        self.data = b''

    def _record(self, step, state, note=''):
        self.history.append(dict(step=step, state=state.value, note=note))
        self.state = state
        self.detail = None

    def _drain(self, timeout):
        import time
        data = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = self.port.read(64)
            if chunk:
                data.extend(chunk)
                if self.log:
                    self.log.write(chunk); self.log.flush()
        self.data = bytes(data)
        state, detail = observe(self.data)
        self.detail = detail
        return state

    def observe(self, timeout=2.0):
        """Passive: read whatever the board is emitting and classify it.
        Sends nothing."""
        state = self._drain(timeout)
        self._record('observe', state, f'{len(self.data)} bytes')
        return state

    def query(self):
        """Non-destructive liveness probe: one newline. Classifies LOADER
        (menu prompt), APP_READY (application prompt) or UNKNOWN (silence).
        Never reboots, never enters a menu, never sends a command."""
        self.port.reset_input_buffer()
        self.port.write(self.PROBE); self.port.flush()
        state = self._drain(3.0)
        self._record('query', state, f'{len(self.data)} bytes')
        return state

    def reset(self):
        """Reboot the CPU with '!' and classify the resulting boot. Destructive
        to a running application; only for a board that is silent or failed."""
        self.port.reset_input_buffer()
        self.port.write(self.RESET_KEY); self.port.flush()
        import time
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            chunk = self.port.read(64)
            if chunk:
                self.data += chunk
                if self.log:
                    self.log.write(chunk); self.log.flush()
                state, detail = observe(self.data)
                if state in (State.APP_READY, State.LOADER, State.FAILED):
                    self._record('reset', state, detail and detail.get('error', ''))
                    self.detail = detail
                    return state
        self._record('reset', State.UNKNOWN, 'no boot output')
        return State.UNKNOWN

    def sram_load(self, program):
        """Program the gateware to SRAM via `program()` (a Board.program
        callable), then classify the fresh banner."""
        program()
        state = self._drain(15.0)
        self._record('sram', state, f'{len(self.data)} bytes')
        return state

    def known(self, allowed=(State.LOADER, State.APP_READY)):
        return self.state in allowed

    def recover(self, program=None, allowed=(State.LOADER, State.APP_READY)):
        """Walk the escalation ladder until the board is in `allowed` or the
        ladder is exhausted. Returns the final state."""
        if self.known(allowed):
            return self.state
        ladder = [self.query, self.reset] + ([lambda: self.sram_load(program)] if program else [])
        for step in ladder:
            state = step()
            if state in allowed:
                return state
            if state is State.UNKNOWN:
                # Silence at this rung means the next rung is the only hope;
                # keep walking but never loop back.
                continue
        return self.state
