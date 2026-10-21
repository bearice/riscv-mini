"""Bounded retries for transient Gowin license-server failures."""
import re
import time
from pathlib import Path

import sys
sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[2] / 'scripts')]
from scripts.modular.storage import write

RETRY_DELAYS = (5, 15, 30)


def license_connection_failure(text):
    """Return evidence from a bounded license context, never an empty log."""
    lines = text.splitlines()
    for line in lines:
        if re.search(r'^\s*(?:ERROR|FATAL).*?(?:\bPR\d+\b|syntax error|cannot open verilog)', line, re.I):
            return None
    for index, line in enumerate(lines):
        if not re.search(r'licen[cs]e|flexnet', line, re.I):
            continue
        context = '\n'.join(lines[max(0, index - 1):index + 4])
        if re.search(r'(?:license|licence).{0,30}(?:has expired|is expired|invalid)|'
                     r'(?:expired|invalid) (?:license|licence)|invalid hostid', context, re.I):
            continue
        if re.search(r'connection\s+(?:time[ -]?out|timed out|refused|failed)|'
                     r'server\s+(?:is\s+)?not responding|'
                     r'(?:cannot|can.t|unable to|failed to)\s+connect\s+to\s+(?:the\s+)?(?:license\s+)?server|'
                     r'license server.{0,40}(?:unavailable|unreachable)|'
                     r'flexnet licensing error:\s*-(?:15|96|97)\b', context, re.I):
            return context.strip()
    return None


def run_with_license_retry(attempt, directory, prefix='gowin', delays=RETRY_DELAYS):
    """attempt(log_path) runs one process and returns its exit code.

    Logs and metadata are retained even when all attempts fail. Callers still
    validate newly generated bitstreams and timing after this function returns.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    records = []
    summary = directory / (prefix + '-attempts.json')
    for index in range(len(delays) + 1):
        log = directory / ('%s-attempt-%d.log' % (prefix, index + 1))
        record = dict(attempt=index + 1, log=log.name)
        records.append(record)
        write(summary, dict(status='running', attempts=records))
        try:
            code = attempt(log)
        except BaseException as error:
            interrupted = isinstance(error, (KeyboardInterrupt, SystemExit))
            record.update(interrupted=interrupted, error=str(error))
            write(summary, dict(status='interrupted' if interrupted else 'failed', attempts=records))
            raise
        evidence = license_connection_failure(log.read_text(encoding='utf-8', errors='replace'))
        record.update(returncode=code, license_connection_failure=evidence)
        if not code and not evidence:
            write(summary, dict(status='succeeded', attempts=records))
            return 0, log
        if evidence and index < len(delays):
            delay = delays[index]
            record['retry_delay_seconds'] = delay
            write(summary, dict(status='retrying', attempts=records))
            print('Gowin license server connection failed; retry %d/%d in %s seconds. Log: %s' %
                  (index + 1, len(delays), delay, log), flush=True)
            try:
                time.sleep(delay)
            except BaseException:
                write(summary, dict(status='interrupted', attempts=records))
                raise
        else:
            code = code or 1  # Some vendor failures may incorrectly exit zero.
            write(summary, dict(status='failed', returncode=code, attempts=records))
            return code, log
