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
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.listener.bind((bind, port))
        self.listener.settimeout(30)

    def close(self):
        self.listener.close()

    def serve_once(self):
        request, client = self.listener.recvfrom(1024)
        fields = request[2:].split(b'\0')
        if request[:2] != b'\0\1' or len(fields) != 3 or fields[0] != self.name.encode() or fields[1].lower() != b'octet' or fields[2]:
            self.listener.sendto(b'\0\5\0\1File unavailable\0', client)
            self.events.append('rejected request')
            return False
        self.events.append('RRQ ' + self.name)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as transfer:
            transfer.bind((self.bind, 0))
            transfer.settimeout(1)
            for block, offset in enumerate(range(0, len(self.data) + 1, 512), 1):
                packet = struct.pack('!HH', 3, block) + self.data[offset:offset + 512]
                for retry in range(8):
                    if self.exercise_retry and block == 1 and retry == 0:
                        # First response withheld: exercise client's RRQ retry.
                        time.sleep(1.2)
                        repeated, sender = self.listener.recvfrom(1024)
                        if repeated != request or sender != client:
                            raise AssertionError('expected identical RRQ retry')
                        self.events.append('RRQ retry observed')
                    transfer.sendto(packet, client)
                    if self.exercise_retry and retry == 0:
                        transfer.sendto(packet, client)
                    deadline = time.monotonic() + 1
                    accepted = False
                    while time.monotonic() < deadline:
                        try:
                            ack, sender = transfer.recvfrom(1024)
                        except socket.timeout:
                            break
                        if sender == client and ack == struct.pack('!HH', 4, block):
                            accepted = True
                            break
                    if accepted:
                        self.events.append(f'ACK {block}')
                        break
                else:
                    raise TimeoutError(f'TFTP block {block} not acknowledged')
                if len(packet) < 516:
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
