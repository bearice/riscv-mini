"""Explicit board benchmark: distinguish PTY throughput from RGB LCD scrolling."""
import argparse
import base64
import json
from pathlib import Path
import re
import time
import serial


def measure(port, lines):
    results = []
    with serial.Serial(port, 115200, timeout=.2) as uart:
        def command(text, timeout=120):
            for value in (text + "\n").encode():
                uart.write(bytes([value]))
                time.sleep(.02)
            data = b""
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                data += uart.read(4096)
                if data.endswith(b"mini-linux# "):
                    return data.decode(errors="replace")
            raise TimeoutError(text)
        command("")
        processes = command("ps -o pid,tty,args")
        paused = re.findall(r"^\s*(\d+)\s+(?:tty1|4,1)\s+top(?:\s|$)", processes, re.MULTILINE)
        for pid in paused:
            command("kill -STOP " + pid)
        try:
            return run_cases(command, lines, paused)
        finally:
            for pid in paused:
                command("kill -CONT " + pid)


def run_cases(command, lines, paused):
    results = []
    for target in ("null", "lcd", "pty_null"):
        payload = {"null": f"seq 1 {lines} >/dev/null",
                   "lcd": f"seq 1 {lines} >/dev/tty1",
                   "pty_null": f"script -q -c 'seq 1 {lines}' /dev/null >/dev/null"}[target]
        text = command("cat /proc/uptime; " + payload + "; cat /proc/uptime")
        stamps = re.findall(r"^([0-9]+\.[0-9]+) [0-9]+\.[0-9]+\r?$", text, re.MULTILINE)
        if len(stamps) != 2:
            raise RuntimeError("Missing timing markers: " + text)
        results.append(dict(target=target, seconds=round(float(stamps[1])-float(stamps[0]), 2), output=text, paused_top=paused))
        print(target, results[-1]["seconds"], flush=True)
        if target == "lcd":
            encoded = command("base64 /dev/vcs1")
            raw = "".join(line.strip() for line in encoded.splitlines()
                          if re.fullmatch(r"[A-Za-z0-9+/=]+", line.strip()))
            screen = base64.b64decode(raw, validate=True).decode()
            rows = [screen[i:i+60].strip() for i in range(0, len(screen), 60)]
            numbers = [int(row) for row in rows if row.isdigit()]
            if not numbers or numbers[-1] != lines or numbers != list(range(numbers[0], lines+1)):
                raise RuntimeError("Scroll text lost or reordered: " + repr(rows))
            results[-1]["lcd_rows"] = rows
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--lines", type=int, default=40)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-scroll-seconds", type=float, default=5)
    args = parser.parse_args()
    results = measure(args.port, args.lines)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    assert next(r for r in results if r["target"] == "lcd")["seconds"] < args.max_scroll_seconds, "LCD scrolling exceeds budget"


if __name__ == "__main__":
    main()
