"""Temporary UDP echo peer for the board's Linux network acceptance."""
import argparse
import socket

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--bind', required=True)
parser.add_argument('--port',type=int,default=0)
args = parser.parse_args()
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
    peer.bind((args.bind, args.port))
    print('UDP echo ready',peer.getsockname()[1], flush=True)
    count = 0
    while True:
        data, address = peer.recvfrom(1500)
        if len(data) != 1000:
            continue
        peer.sendto(data, address)
        count += 1
        print('echo', count, address, len(data), flush=True)
