"""Board session for the AE350: one owner for programmer, serial and CPU JTAG.

The session makes the lifecycle explicit: opening the serial port and the debug
module, programming the FPGA, halting/resuming the core, and closing everything
while recording cleanup failures instead of hiding them.
"""
import json
import subprocess
import time
import os
from pathlib import Path

import sys
sys.path[:0] = [str(Path(__file__).resolve().parents[3]), str(Path(__file__).resolve().parents[3] / 'scripts')]
from scripts.tools.build_records import ROOT
from tools.boot_session import programmer_succeeded
from tools.programmer_cable import resolve_location


class SessionError(RuntimeError):
    pass


class HardwareSession:
    def __init__(self, op, port='COM9', cpu_jtag_location=None, location=None, dry_run=False):
        self.op = op
        self.tools = json.loads((ROOT / '.tools.local.json').read_text(encoding='utf-8'))
        self.port_name = port
        self.cpu_location = cpu_jtag_location
        self.location = location
        self.dry_run = dry_run
        self.port = None
        self._cpu = None
        self.cleanup_errors = []
        self.programmed = None
        self._lock = None
        self._logs = []
        self.op.report['session'] = dict(port=port, cpu_jtag_location=cpu_jtag_location, dry_run=dry_run)

    # -- devices ---------------------------------------------------------
    def _acquire(self):
        if self._lock is None:
            from modular.storage import workspace_lock
            lock = workspace_lock(ROOT / 'build/work/hardware/mega138k_ae350')
            lock.__enter__()
            self._lock = lock

    def open_log(self, name):
        log = (self.op.path / name).open('wb')
        self._logs.append(log)
        return log

    def open_serial(self):
        if self.dry_run or self.port is not None:
            return self.port
        import serial
        self._acquire()
        self.port = serial.Serial(self.port_name, 115200, timeout=0.2)
        self.op.report['session']['serial_opened'] = True
        self.op.save()
        return self.port

    def open_cpu(self):
        if self.dry_run:
            return None
        if self._cpu is None:
            from mini_ops.mega138k_ae350.ae350_jtag import CPUJTAG
            if self.cpu_location is None:
                raise SessionError('this operation needs --cpu-jtag-location')
            self._acquire()
            library = 'ftd2xx.dll' if os.name == 'nt' else 'libftd2xx.so'
            self._cpu = CPUJTAG(self.cpu_location,
                                Path(self.tools['programmer']).with_name(library))
        return self._cpu

    def halt(self):
        cpu = self.open_cpu()
        cpu.halt()
        return cpu

    def resume(self):
        if self._cpu is not None:
            self._cpu.resume()

    def restart_rom(self, entry=0x80000000, expected_rom=None):
        cpu = self.halt()
        if expected_rom is not None:
            if cpu.read_memory(entry, len(expected_rom)) != expected_rom:
                raise SessionError('ROM readback differs from the selected bootloader')
            self.op.record('restart-rom-match', bytes=len(expected_rom))
        cpu.register(0x7b1, entry)
        self.resume()

    def read_word(self, address):
        return int.from_bytes(self.halt().read_memory(address, 4), 'little')

    # -- programming -----------------------------------------------------
    def program(self, fs):
        if self.dry_run:
            return True
        self._acquire()
        location = resolve_location(self.tools['programmer'], self.location,
                                    identity=0x0001081B, log_path=self.op.path / 'cables.log')
        command = [self.tools['programmer'], '--location', location, '--device', 'GW5AST-138B',
                   '--frequency', '2MHz', '--operation_index', '2', '--fsFile', str(fs)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        (self.op.path / 'sram.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        ok = programmer_succeeded(result)
        self.op.record('sram', command=command, programmer_passed=ok)
        if not ok:
            raise SessionError('SRAM programming failed; inspect sram.log')
        self.programmed = Path(fs)
        return ok

    # -- teardown --------------------------------------------------------
    def close(self, fail=True):
        for log in self._logs:
            try:
                log.close()
            except Exception as error:
                self.cleanup_errors.append('log: %s' % error)
        self._logs.clear()
        if self._cpu is not None:
            try:
                self._cpu.close(resume=True)
                self.op.report['session']['cpu_final_state'] = 'running'
            except Exception as error:
                self.cleanup_errors.append('cpu: %s' % error)
            self._cpu = None
        if self.port is not None:
            try:
                self.port.close()
            except Exception as error:
                self.cleanup_errors.append('serial: %s' % error)
            self.port = None
        if self._lock is not None:
            try:
                self._lock.__exit__(None, None, None)
            except Exception as error:
                self.cleanup_errors.append('lock: %s' % error)
            self._lock = None
        if self.cleanup_errors:
            self.op.record('cleanup', errors=self.cleanup_errors, passed=False)
            self.op.report['passed'] = False
        self.op.report['session']['cleanup_errors'] = self.cleanup_errors
        self.op.save()
        if fail and self.cleanup_errors:
            raise SessionError('hardware cleanup failed: ' + '; '.join(self.cleanup_errors))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close(fail=exc[0] is None)
        return False
