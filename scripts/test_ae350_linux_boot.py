import os
import sys
import time
import socket
import struct
import threading
import serial

HOST_IP = "192.168.1.1"
SERIAL_PORT = "/dev/ttyUSB3"
BAUD = 115200

KERNEL_PATH = "/home/bearice/riscv-mini-linux/obj-minimal/arch/riscv/boot/Image"
DTB_PATH = "/home/bearice/workspace/fpga/riscv-mini/build/mega138k_ae350.dtb"
OPENSBI_PATH = "/home/bearice/workspace/fpga/riscv-mini/build/opensbi-ae350/platform/generic/firmware/fw_jump.bin"

FILES = {
    "Image": open(KERNEL_PATH, "rb").read(),
    "mega138k_ae350.dtb": open(DTB_PATH, "rb").read(),
    "fw_jump.bin": open(OPENSBI_PATH, "rb").read(),
}

print(f"Loaded files: Image ({len(FILES['Image'])} bytes), DTB ({len(FILES['mega138k_ae350.dtb'])} bytes), OpenSBI ({len(FILES['fw_jump.bin'])} bytes)")

class TftpServer(threading.Thread):
    def __init__(self, bind_ip):
        super().__init__(daemon=True)
        self.bind_ip = bind_ip
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((bind_ip, 69))
        self.running = True

    def run(self):
        print(f"TFTP Server listening on {self.bind_ip}:69")
        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
            except Exception:
                break
            if len(data) < 4:
                continue
            opcode = struct.unpack("!H", data[:2])[0]
            if opcode == 1: # RRQ
                parts = data[2:].split(b"\x00")
                filename = parts[0].decode('ascii', errors='replace')
                # Parse options
                blksize = 512
                opts = parts[2:]
                for i in range(0, len(opts) - 1, 2):
                    if opts[i].lower() == b'blksize':
                        try:
                            blksize = int(opts[i+1])
                        except ValueError:
                            pass
                print(f"[TFTP] RRQ for '{filename}' from {addr}, blksize={blksize}")
                if filename in FILES:
                    threading.Thread(target=self.serve_file, args=(addr, FILES[filename], blksize), daemon=True).start()
                else:
                    print(f"[TFTP] File '{filename}' not found!")
                    err = struct.pack("!HH", 5, 1) + b"File not found\x00"
                    self.sock.sendto(err, addr)

    def serve_file(self, client_addr, file_data, blksize):
        tsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tsock.bind((self.bind_ip, 0))
        tsock.settimeout(1.0)
        t0 = time.time()

        # If client requested blksize != 512, send OACK
        if blksize != 512:
            oack = struct.pack("!H", 6) + b"blksize\x00" + str(blksize).encode() + b"\x00"
            tsock.sendto(oack, client_addr)
            # Wait for ACK block 0
            for _ in range(5):
                try:
                    ack_data, _ = tsock.recvfrom(1024)
                    if len(ack_data) >= 4 and struct.unpack("!HH", ack_data[:4]) == (4, 0):
                        break
                except socket.timeout:
                    tsock.sendto(oack, client_addr)

        block_num = 1
        offset = 0
        total_len = len(file_data)

        while offset < total_len or (offset == total_len and block_num == 1):
            chunk = file_data[offset:offset + blksize]
            data_pkt = struct.pack("!HH", 3, block_num & 0xffff) + chunk

            for retry in range(5):
                tsock.sendto(data_pkt, client_addr)
                try:
                    ack, _ = tsock.recvfrom(1024)
                    if len(ack) >= 4:
                        op, ack_blk = struct.unpack("!HH", ack[:4])
                        if op == 4 and ack_blk == (block_num & 0xffff):
                            break
                except socket.timeout:
                    pass
            else:
                print(f"[TFTP] Timeout delivering block {block_num} to {client_addr}")
                tsock.close()
                return

            offset += len(chunk)
            block_num += 1
            if len(chunk) < blksize:
                break

        elapsed = time.time() - t0
        rate = (total_len / 1024 / 1024) / elapsed if elapsed > 0 else 0
        print(f"[TFTP] Completed {total_len} bytes in {elapsed:.2f}s ({rate:.2f} MB/s)")
        tsock.close()

# Start TFTP server
tftp = TftpServer(HOST_IP)
tftp.start()

# Connect to serial port
ser = serial.Serial(SERIAL_PORT, BAUD, timeout=0.1)

def send_cmd(cmd):
    print(f"\n>>> {cmd}")
    ser.write((cmd + "\r\n").encode())

def wait_for(marker, timeout=30):
    buf = ""
    t_end = time.time() + timeout
    while time.time() < t_end:
        chunk = ser.read(1024).decode('latin1', errors='replace')
        if chunk:
            sys.stdout.write(chunk)
            sys.stdout.flush()
            buf += chunk
            if marker in buf:
                return True
    return False

# Get BIOS prompt
time.sleep(0.2)
ser.write(b"\r\n")
if not wait_for("> ", 5):
    print("Failed to get BIOS prompt!")
    sys.exit(1)

# TFTP load Kernel Image
send_cmd("tftp 00400000 Image")
if not wait_for("TFTP OK", 15):
    print("Failed to load Image!")
    sys.exit(1)
wait_for("> ", 3)

# TFTP load DTB
send_cmd("tftp 01f00000 mega138k_ae350.dtb")
if not wait_for("TFTP OK", 5):
    print("Failed to load DTB!")
    sys.exit(1)
wait_for("> ", 3)

# TFTP load OpenSBI
send_cmd("tftp 01000000 fw_jump.bin")
if not wait_for("TFTP OK", 10):
    print("Failed to load OpenSBI!")
    sys.exit(1)
wait_for("> ", 3)

# Boot via OpenSBI
send_cmd("go 01000000 01f00000")

# Observe execution
print("\n=== Observing Boot Log (up to 60s) ===")
t_end = time.time() + 60
buf = ""
while time.time() < t_end:
    chunk = ser.read(1024).decode('latin1', errors='replace')
    if chunk:
        sys.stdout.write(chunk)
        sys.stdout.flush()
        buf += chunk
        if "linux-mini>" in buf or "mini-linux#" in buf or "LINUX USERSPACE PASS" in buf:
            print("\n\n*** LINUX BOOT SUCCESS! ***")
            break
