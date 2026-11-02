#!/usr/bin/env python3
import time
import subprocess
import socket
import zlib
from pathlib import Path
from contextlib import contextmanager
import serial

ROOT = Path("/home/bearice/workspace/fpga/riscv-mini")
HOST_IP = "192.168.1.1"
SERIAL_PORT = "/dev/ttyUSB3"
TFTP_RUNNER = "/tmp/tftp_runner.sh"

KERNEL_FILE = Path("/home/bearice/riscv-mini-linux/obj-minimal/arch/riscv/boot/Image")
DTB_FILE = ROOT / "build/mega138k_ae350.dtb"
OPENSBI_FILE = ROOT / "build/opensbi-ae350/platform/generic/firmware/fw_jump.bin"

@contextmanager
def tftp_server(file_path, name):
    cmd = [
        TFTP_RUNNER,
        "-u",
        str(ROOT / "scripts/primer20k_vexriscv/bios_tftp.py"),
        "--bind", HOST_IP,
        "--file", str(file_path.resolve()),
        "--name", name
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        t0 = time.time()
        ready = False
        while time.time() - t0 < 5.0:
            line = proc.stdout.readline()
            if b"TFTP ready" in line:
                ready = True
                print(f"[HOST] {line.decode().strip()}", flush=True)
                break
            time.sleep(0.05)
        if not ready:
            raise RuntimeError("TFTP server failed to start")
        yield
    finally:
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=2)

def main():
    port = serial.Serial(SERIAL_PORT, 115200, timeout=0.2)
    port.reset_input_buffer()

    def send(cmd):
        print(f"[CMD] {cmd}", flush=True)
        port.write((cmd + "\r\n").encode())

    def read_until(marker, timeout=30.0):
        buf = bytearray()
        t0 = time.time()
        while time.time() - t0 < timeout:
            chunk = port.read(port.in_waiting or 1)
            if chunk:
                buf.extend(chunk)
                print(chunk.decode("ascii", errors="replace"), end="", flush=True)
                if marker in buf:
                    return bytes(buf)
            else:
                time.sleep(0.01)
        raise TimeoutError(f"Timeout waiting for {marker!r}")

    # Wake up BIOS prompt
    send("")
    read_until(b"\n> ", timeout=5.0)

    # Configure IPs
    send("set ip 192.168.1.50")
    read_until(b"\n> ", timeout=5.0)
    send(f"set server {HOST_IP}")
    read_until(b"\n> ", timeout=5.0)

    # 1. Transfer Kernel Image (0x00400000)
    print("\n--- 1. Transferring Linux Image ---", flush=True)
    with tftp_server(KERNEL_FILE, "Image"):
        send("tftp 00400000 Image")
        read_until(b"\n> ", timeout=60.0)

    send(f"crc32 00400000 {len(KERNEL_FILE.read_bytes()):x}")
    reply = read_until(b"\n> ", timeout=10.0)
    expected_crc = f"{zlib.crc32(KERNEL_FILE.read_bytes()):08x}".encode()
    if expected_crc not in reply.lower():
        raise RuntimeError(f"Kernel CRC mismatch! Expected {expected_crc}, got {reply!r}")
    print(f"[OK] Kernel CRC verified: {expected_crc.decode()}", flush=True)

    # 2. Transfer DTB (0x01f00000)
    print("\n--- 2. Transferring DTB ---", flush=True)
    with tftp_server(DTB_FILE, "mega138k_ae350.dtb"):
        send("tftp 01f00000 mega138k_ae350.dtb")
        read_until(b"\n> ", timeout=15.0)

    send(f"crc32 01f00000 {len(DTB_FILE.read_bytes()):x}")
    reply = read_until(b"\n> ", timeout=5.0)
    expected_crc = f"{zlib.crc32(DTB_FILE.read_bytes()):08x}".encode()
    if expected_crc not in reply.lower():
        raise RuntimeError(f"DTB CRC mismatch! Expected {expected_crc}, got {reply!r}")
    print(f"[OK] DTB CRC verified: {expected_crc.decode()}", flush=True)

    # 3. Transfer OpenSBI (0x01000000)
    print("\n--- 3. Transferring OpenSBI fw_jump.bin ---", flush=True)
    with tftp_server(OPENSBI_FILE, "fw_jump.bin"):
        send("tftp 01000000 fw_jump.bin")
        read_until(b"\n> ", timeout=20.0)

    send(f"crc32 01000000 {len(OPENSBI_FILE.read_bytes()):x}")
    reply = read_until(b"\n> ", timeout=5.0)
    expected_crc = f"{zlib.crc32(OPENSBI_FILE.read_bytes()):08x}".encode()
    if expected_crc not in reply.lower():
        raise RuntimeError(f"OpenSBI CRC mismatch! Expected {expected_crc}, got {reply!r}")
    print(f"[OK] OpenSBI CRC verified: {expected_crc.decode()}", flush=True)

    # 4. Jump to OpenSBI
    print("\n--- 4. Executing: go 01000000 01f00000 ---", flush=True)
    send("go 01000000 01f00000")

    # Monitor output for 60 seconds
    t0 = time.time()
    while time.time() - t0 < 60.0:
        chunk = port.read(port.in_waiting or 1)
        if chunk:
            print(chunk.decode("ascii", errors="replace"), end="", flush=True)
        else:
            time.sleep(0.01)

if __name__ == "__main__":
    main()
