"""Shared process and database setup for local demos and integration checks."""
import logging
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger(__name__)


def wait_for(test, seconds=20, description='condition', processes=()):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if any(p.poll() is not None for p in processes):
            raise RuntimeError(f'A process exited while waiting for {description}; inspect the run logs.')
        if test():
            return
        time.sleep(.1)
    raise TimeoutError(f'Timed out waiting for {description}.')


def port_ready(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=.2):
            return True
    except OSError:
        return False


def fresh_database(target):
    """Copy the closed sample database and clear only the copy's event tables."""
    import duckdb
    source = ROOT / 'database/assignment2.duckdb'
    if target.exists():
        raise FileExistsError(target)
    if Path(str(source) + '.wal').exists():
        raise ValueError('Stop the sample database writer before making a demo copy')
    shutil.copy2(source, target)
    with duckdb.connect(str(target)) as db:
        for table in ('meter_reading', 'current_price', 'charging_session', 'mqtt_event', 'mqtt_rejection'):
            db.execute(f'DELETE FROM core.{table}')


def stop_process(process, stop_file=None, timeout=35):
    """Ask a subscriber to drain its queue before falling back to forced shutdown."""
    if process.poll() is not None:
        return process.returncode
    try:
        if stop_file is not None:
            stop_file.touch()
        elif os.name == 'nt':
            process.terminate()
        else:
            process.send_signal(signal.SIGINT)
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        LOG.warning('Process %s did not stop in time; terminating it', process.pid)
    except ProcessLookupError:
        return process.wait(timeout=5)
    except OSError as exc:
        LOG.warning('Could not request shutdown for process %s: %s', process.pid, exc)
    process.terminate()
    try:
        return process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait(timeout=5)


class Processes:
    """Own child processes and log handles, including cleanup after a failed launch."""
    def __init__(self, directory):
        self.directory = directory
        self.processes = []
        self.logs = []
        self.stop_files = {}

    def launch(self, name, arguments, stop_file=None):
        log = (self.directory / (name + '.log')).open('w', encoding='utf-8')
        self.logs.append(log)
        if stop_file is not None:
            if stop_file.exists():
                raise FileExistsError(f'Use a new stop file for each subscriber: {stop_file}')
            arguments = [*arguments, '--stop-file', str(stop_file)]
        # The launcher handles Ctrl+C and stops children in dependency order.
        options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        process = subprocess.Popen([sys.executable, *arguments], cwd=ROOT,
                                   stdout=log, stderr=subprocess.STDOUT, **options)
        self.processes.append(process)
        self.stop_files[process] = stop_file
        return process

    def stop(self, process):
        return stop_process(process, self.stop_files[process])

    def close(self):
        failures = []
        for process in reversed(self.processes):
            try:
                self.stop(process)
            except Exception as exc:
                LOG.exception('Could not stop process %s', process.pid)
                failures.append(exc)
        for log in self.logs:
            log.close()
        if failures:
            raise RuntimeError('Some child processes could not be stopped') from failures[0]
