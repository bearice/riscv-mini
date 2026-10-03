"""Board regression: ls must use BIOS listing output and return to its prompt.

Run with the board already at the BIOS prompt and an SD card inserted.
Serial output alone does not verify physical LCD rendering.
"""
import argparse
import time
import serial

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--port', default='COM4')
args = parser.parse_args()
with serial.Serial(args.port, 115200, timeout=.1) as port:
    port.reset_input_buffer()
    port.write(b'ls\r')
    output = bytearray()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        output.extend(port.read(4096))
        if output.endswith(b'> '):
            break
text = output.decode(errors='replace').replace('\r', '')
print(text)
assert 'SD /\n' in text, 'missing BIOS directory header (old UART-only path?)'
assert 'LS: ' in text and ' entries\n' in text, 'missing completed directory count'
assert text.endswith('> '), 'ls did not return to the BIOS prompt'
print('PASS BIOS ls header/count/prompt; physical LCD needs separate confirmation')
