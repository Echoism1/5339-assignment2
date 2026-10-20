"""Real-broker recovery tests in scratch DBs; never alter the submitted replay DB."""
import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import duckdb
import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def wait_for(predicate, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.1)
    raise TimeoutError('test condition timed out')


def port_ready(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.2):
            return True
    except OSError:
        return False


def publish(port, topic, messages, retain=False):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect('127.0.0.1', port)
    client.loop_start()
    try:
        for payload in messages:
            info = client.publish(topic, payload, qos=1, retain=retain)
            info.wait_for_publish(10)
            assert info.is_published()
    finally:
        client.disconnect()
        client.loop_stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--a1-db', required=True)
    args = parser.parse_args()
    scratch = ROOT / 'verification/task2/scratch'
    scratch.mkdir(parents=True, exist_ok=True)
    run = scratch / str(time.time_ns())
    run.mkdir()
    port = 18884
    topic = 'comp5339/recovery/events'
    events = (ROOT / 'data/window.jsonl').read_text(encoding='utf-8').splitlines()
    db = run / 'recovery.duckdb'
    subprocess.run([PYTHON, 'subscriber.py', 'init', '--a1-db', args.a1_db, '--db', str(db)], cwd=ROOT, check=True)
    handles, children = [], []

    def start(command, log):
        handle = (run / log).open('w', encoding='utf-8')
        handles.append(handle)
        process = subprocess.Popen([PYTHON, *command], cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
        children.append(process)
        return process

    def broker():
        process = start(['tools/dev_broker.py', '--port', str(port)], f'broker-{len(children)}.log')
        wait_for(lambda: port_ready(port))
        return process

    def listen(seconds, name):
        process = start(['subscriber.py', 'listen', '--db', str(db), '--port', str(port),
                         '--topic', topic, '--client-id', 'task2-recovery', '--run-seconds', str(seconds),
                         '--summary', str(run / f'{name}.json')], f'{name}.log')
        wait_for(lambda: 'READY' in (run / f'{name}.log').read_text(encoding='utf-8'))
        return process

    try:
        b = broker()
        # A retained event on subscription is quarantined, not mistaken for replay history.
        publish(port, topic, [events[0]], retain=True)
        s = listen(12, 'first')
        publish(port, topic, [''], retain=True)  # clear the retained broker slot
        publish(port, topic, events[:10] + events[:10] + ['{bad', '\u00e9'])
        publish(port, topic + '/wrong', events[10:15])
        wait_for(lambda: (run / 'first.log').read_text(encoding='utf-8').count('quarantined') >= 4)
        b.terminate()
        b.wait(10)
        b = broker()  # Broker session is gone; on_connect must resubscribe.
        wait_for(lambda: (run / 'first.log').read_text(encoding='utf-8').count('READY') >= 2)
        publish(port, topic, events[10:20])
        assert s.wait(20) == 0
        first = json.loads((run / 'first.json').read_text())
        assert first['outcomes'].get('stored') == 20, first
        assert first['outcomes'].get('duplicate') == 10, first
        assert first['outcomes'].get('rejected', 0) >= 3, first
        # Restart subscriber against the SAME committed DB and replay duplicates.
        s = listen(4, 'second')
        publish(port, topic, events[:20])
        assert s.wait(10) == 0
        second = json.loads((run / 'second.json').read_text())
        assert second['outcomes'] == {'duplicate': 20}, second
        with duckdb.connect(str(db), read_only=True) as connection:
            rows = connection.execute('SELECT event_id,payload FROM core.mqtt_event').fetchall()
            assert {r[0]: json.loads(r[1]) for r in rows} == {json.loads(e)['event_id']: json.loads(e) for e in events[:20]}
        # Deterministically slow only the test worker to exercise bounded backpressure.
        snippet = (
            'import time; from ingestion.storage import Store; '
            'original=Store.receive; '
            'Store.receive=lambda self,*a,**kw:(time.sleep(0.1),original(self,*a,**kw))[1]; '
            'import subscriber; raise SystemExit(subscriber.main())'
        )
        slow = start(['-c', snippet, 'listen', '--db', str(db), '--port', str(port),
                      '--topic', topic, '--client-id', 'task2-recovery', '--queue-size', '1',
                      '--run-seconds', '6'], 'overflow.log')
        wait_for(lambda: 'READY' in (run / 'overflow.log').read_text(encoding='utf-8'))
        publish(port, topic, events[20:40])
        assert slow.wait(15) == 1
        assert 'bounded queue full' in (run / 'overflow.log').read_text(encoding='utf-8')
        s = listen(4, 'after_overflow')
        publish(port, topic, events[20:40])
        assert s.wait(10) == 0
        with duckdb.connect(str(db), read_only=True) as connection:
            rows = connection.execute('SELECT event_id,payload FROM core.mqtt_event').fetchall()
            assert {r[0]: json.loads(r[1]) for r in rows} == {json.loads(e)['event_id']: json.loads(e) for e in events[:40]}
        result = dict(passed=True, first=first['outcomes'], second=second['outcomes'],
                      checks=['retained quarantine', 'malformed quarantine', 'wrong topic ignored',
                              'QoS 1 duplicates', 'broker restart and resubscription',
                              'subscriber restart and durable dedup', 'exact saved payloads',
                              'bounded queue overflow fails visibly', 'replay after overflow recovers all events'],
                      scratch=str(run.relative_to(ROOT)))
        (ROOT / 'verification/task2/recovery_verification.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result, indent=2))
    finally:
        for process in children:
            if process.poll() is None:
                process.terminate()
                process.wait(10)
        for handle in handles:
            handle.close()


if __name__ == '__main__':
    main()
