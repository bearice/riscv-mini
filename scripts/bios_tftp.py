"""Serve one BIOS payload over TFTP octet on an explicitly chosen local IPv4 address.

The server exports exactly one file, never accepts writes, and changes no network
or firewall settings. Use an isolated board/host link. No authentication is provided.
"""
import argparse
import socket
import struct
import time
from pathlib import Path


class PayloadServer:
    def __init__(self, bind, data, name='BOOT.RPB', port=69, exercise_retry=False):
        self.bind, self.data, self.name = bind, data, name
        self.exercise_retry = exercise_retry
        self.events = []
        # Per-transfer timing, populated by serve_once when stats=True. Lets any
        # caller (uboot_verify --tftp-stats, a benchmark) measure where the time
        # goes without a bespoke server. Reset at the start of each serve_once.
        self.stats = None
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.listener.bind((bind, port))
        self.listener.settimeout(30)

    def close(self):
        self.listener.close()

    def serve_once(self):
        request, client = self.listener.recvfrom(1024)
        self.stats = {'blocks': 0, 'sent': 0, 'retries': 0, 'ack_ms': [], 'total_s': 0.0, 'blksize': 512}
        fields = request[2:].split(b'\0')
        # RRQ = opcode filename\0 mode\0 [opt\0 val\0]*. Accept options after the
        # mode; only blksize (RFC 2348) is honoured, capped at 1428 so a DATA
        # packet stays inside one Ethernet MTU (no IP fragmentation).
        if request[:2] != b'\0\1' or len(fields) < 3 or fields[0] != self.name.encode() or fields[1].lower() != b'octet':
            self.listener.sendto(b'\0\5\0\1File unavailable\0', client)
            self.events.append('rejected request')
            return False
        self.events.append('RRQ ' + self.name)
        blksize = 512
        opts = fields[2:]
        i = 0
        while i + 1 < len(opts) and opts[i]:
            if opts[i].lower() == b'blksize':
                try:
                    v = int(opts[i + 1])
                    if 18 <= v <= 1428:
                        blksize = v
                except ValueError:
                    pass
            i += 2
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as transfer:
            transfer.bind((self.bind, 0))
            transfer.settimeout(0.3)
            t_start = time.monotonic()
            if blksize != 512:
                # OACK (opcode 6) then wait for the client's ACK 0 before DATA 1.
                oack = struct.pack('!H', 6) + b'blksize\0' + str(blksize).encode() + b'\0'
                transfer.sendto(oack, client)
                self.events.append(f'OACK blksize={blksize}')
                self.stats['blksize'] = blksize
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    try:
                        ack, sender = transfer.recvfrom(1024)
                    except socket.timeout:
                        break
                    if sender == client and ack == struct.pack('!HH', 4, 0):
                        break
                else:
                    raise TimeoutError('TFTP ACK 0 not received')
            for block, offset in enumerate(range(0, len(self.data) + 1, blksize), 1):
                packet = struct.pack('!HH', 3, block) + self.data[offset:offset + blksize]
                for retry in range(100):
                    if self.exercise_retry and block == 1 and retry == 0:
                        # First response withheld: exercise client's RRQ retry.
                        time.sleep(0.2)
                        repeated, sender = self.listener.recvfrom(1024)
                        if repeated != request or sender != client:
                            raise AssertionError('expected identical RRQ retry')
                        self.events.append('RRQ retry observed')
                    self.stats['sent'] += 1
                    if retry:
                        self.stats['retries'] += 1
                    t_send = time.monotonic()
                    transfer.sendto(packet, client)
                    if self.exercise_retry and retry == 0:
                        transfer.sendto(packet, client)
                    deadline = time.monotonic() + 0.3
                    accepted = False
                    while time.monotonic() < deadline:
                        try:
                            ack, sender = transfer.recvfrom(1024)
                        except socket.timeout:
                            break
                        if sender == client and ack == struct.pack('!HH', 4, block):
                            accepted = True
                            self.stats['blocks'] += 1
                            self.stats['ack_ms'].append((time.monotonic() - t_send) * 1000.0)
                            break
                    if accepted:
                        self.events.append(f'ACK {block}')
                        # No pacing: the protocol is lock-step (we only send the
                        # next DATA after this block's ACK), so at most one frame
                        # is ever outstanding and the shallow board RX ring cannot
                        # be overrun. A time.sleep() here was pure cost: on Windows
                        # time.sleep(0.001) actually blocks ~15.6 ms (system timer
                        # granularity), which alone added ~38 s to a 2540-block
                        # transfer. Measured: with the sleep, 39.3 s / 32 KiB/s;
                        # without it, 5.3 s / 239 KiB/s, zero retransmits.
                        break
                else:
                    raise TimeoutError(f'TFTP block {block} not acknowledged')
                if len(packet) < blksize + 4:
                    self.stats['total_s'] = time.monotonic() - t_start
                    if self.exercise_retry:
                        # Replay final DATA, ensuring client dallies after its ACK.
                        transfer.setblocking(False)
                        try:
                            while True:
                                transfer.recvfrom(1024)
                        except BlockingIOError:
                            pass
                        transfer.settimeout(1)
                        transfer.sendto(packet, client)
                        while True:
                            ack, sender = transfer.recvfrom(1024)
                            if sender == client and ack == struct.pack('!HH', 4, block):
                                self.events.append('final duplicate acknowledged')
                                break
                    return True
        raise AssertionError('missing terminating DATA')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', required=True, help='IPv4 address of the NIC connected to Dock')
    parser.add_argument('--file', type=Path, required=True)
    parser.add_argument('--name', default='BOOT.RPB')
    args = parser.parse_args()
    server = PayloadServer(args.bind, args.file.read_bytes(), args.name)
    try:
        print(f'TFTP ready on {args.bind}:69; exporting {args.name}', flush=True)
        while True:
            try:
                server.serve_once()
                print('\n'.join(server.events), flush=True)
                server.events.clear()
            except socket.timeout:
                pass
    except KeyboardInterrupt:
        pass
    finally:
        server.close()


if __name__ == '__main__':
    main()
