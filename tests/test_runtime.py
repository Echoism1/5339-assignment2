"""Snapshot file failures, graceful subscriber shutdown and child cleanup."""
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import duckdb

from analytics.queries import publish_snapshot
import subscriber
from tools.runtime import ROOT, Processes, fresh_database, stop_process


class SnapshotWriteTests(unittest.TestCase):
    def test_windows_lock_releases_before_retry_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'snapshot.json'
            target.write_text('{"old": true}', encoding='utf-8')
            from os import replace
            attempts = 0

            def locked(source, destination):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    self.assertEqual(json.loads(target.read_text()), {'old': True})
                    raise PermissionError('Reader holds the file')
                replace(source, destination)

            with patch('analytics.queries.snapshot', return_value={'new': True}), \
                    patch('analytics.queries.sys.platform', 'win32'), \
                    patch('analytics.queries.os.replace', side_effect=locked), \
                    patch('analytics.queries.time.sleep'):
                publish_snapshot(None, target)
            self.assertEqual(attempts, 3)
            self.assertEqual(json.loads(target.read_text()), {'new': True})
            self.assertEqual(list(target.parent.glob('*.tmp')), [])

    def test_persistent_lock_keeps_previous_file_and_removes_temporary(self):
        for platform, attempts in [('win32', 8), ('linux', 1)]:
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'snapshot.json'
                target.write_text('{"old": true}', encoding='utf-8')
                with patch('analytics.queries.snapshot', return_value={'new': True}), \
                        patch('analytics.queries.sys.platform', platform), \
                        patch('analytics.queries.os.replace', side_effect=PermissionError('Locked')) as replace, \
                        patch('analytics.queries.time.sleep'), self.assertRaises(PermissionError):
                    publish_snapshot(None, target)
                self.assertEqual(replace.call_count, attempts)
                self.assertEqual(json.loads(target.read_text()), {'old': True})
                self.assertEqual(list(target.parent.glob('*.tmp')), [])


class SubscriberShutdownTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.database = self.directory / 'events.duckdb'
        fresh_database(self.database)
        self.args = SimpleNamespace(db=self.database, host='localhost', port=1883,
                                    topic='test/events', client_id='test', queue_size=32,
                                    run_seconds=3, snapshot=self.directory/'snapshot.json',
                                    snapshot_interval=.001, summary=self.directory/'summary.json',
                                    stop_file=self.directory/'stop')
        self.events = (ROOT/'data/window.jsonl').read_bytes().splitlines()[:20]
        self.client = Mock()
        self.client.subscribe.return_value = (0, 1)
        self.client.ack.return_value = 0
        self.client.loop_start.side_effect = self.deliver
        self.client.disconnect.side_effect = lambda: self.client.on_disconnect(
            self.client, None, None, 0, None)

    def deliver(self):
        reason = SimpleNamespace(is_failure=False, value=1)
        self.client.on_connect(self.client, None, None, reason, None)
        self.client.on_subscribe(self.client, None, 1, [reason], None)
        for mid, payload in enumerate(self.events):
            self.client.on_message(self.client, None, SimpleNamespace(
                mid=mid, topic=self.args.topic, payload=payload, retain=False, qos=1))

    def run_subscriber(self):
        with patch('subscriber.mqtt.Client', return_value=self.client), patch('subscriber.signal.signal'):
            return subscriber.run(self.args)

    def assert_saved(self):
        with duckdb.connect(str(self.database), read_only=True) as db:
            actual = {json.loads(row[0])['event_id'] for row in db.execute('SELECT payload FROM core.mqtt_event').fetchall()}
        self.assertEqual(actual, {json.loads(payload)['event_id'] for payload in self.events})
        result = json.loads(self.args.summary.read_text())
        self.assertEqual(result['failures'], [])
        self.assertEqual(result['outcomes'], {'stored': len(self.events)})

    def test_snapshot_failure_does_not_interrupt_commits_or_acknowledgements(self):
        def acknowledge(*args):
            if self.client.ack.call_count == len(self.events):
                self.args.stop_file.touch()
            return 0

        self.client.ack.side_effect = acknowledge
        with patch('analytics.queries.os.replace', side_effect=PermissionError('Locked')), \
                self.assertLogs('subscriber', level='WARNING') as logs:
            self.assertEqual(self.run_subscriber(), 0)
        self.assert_saved()
        self.assertEqual(self.client.ack.call_count, len(self.events))
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(list(self.directory.glob('*.tmp')), [])

    def test_stop_file_drains_queued_messages_and_publishes_final_snapshot(self):
        original_receive = subscriber.Store.receive

        def slow_receive(store, *args):
            time.sleep(.025)
            return original_receive(store, *args)

        def deliver_then_stop():
            self.deliver()
            self.args.stop_file.touch()

        self.client.loop_start.side_effect = deliver_then_stop
        with patch('subscriber.Store.receive', slow_receive):
            self.assertEqual(self.run_subscriber(), 0)
        self.assert_saved()
        self.assertEqual(json.loads(self.args.snapshot.read_text())['coverage']['received_events'], len(self.events))

    def test_snapshot_query_error_remains_visible(self):
        with patch('subscriber.publish_snapshot', side_effect=ValueError('Invalid snapshot data')), \
                self.assertLogs('subscriber', level='ERROR'):
            self.assertEqual(self.run_subscriber(), 1)
        self.client.loop_start.assert_not_called()


class ProcessCleanupTests(unittest.TestCase):
    def test_windows_subscriber_uses_stop_file_without_signals(self):
        with tempfile.TemporaryDirectory() as directory:
            stop_file = Path(directory)/'stop'
            process = Mock()
            process.poll.return_value = None
            process.wait.side_effect = lambda **kwargs: 0 if stop_file.exists() else 1
            with patch('tools.runtime.os.name', 'nt'):
                self.assertEqual(stop_process(process, stop_file), 0)
            process.send_signal.assert_not_called()
            process.terminate.assert_not_called()

    def test_platform_specific_shutdown(self):
        for platform in ('nt', 'posix'):
            with self.subTest(platform=platform):
                process = Mock()
                process.poll.return_value = None
                with patch('tools.runtime.os.name', platform):
                    stop_process(process)
                if platform == 'nt':
                    process.terminate.assert_called_once()
                    process.send_signal.assert_not_called()
                else:
                    process.send_signal.assert_called_once_with(signal.SIGINT)

    def test_timeout_escalates_to_terminate_then_kill(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('child', 1),
                                    subprocess.TimeoutExpired('child', 5), -9]
        with self.assertLogs('tools.runtime', level='WARNING'):
            self.assertEqual(stop_process(process, Mock(), timeout=1), -9)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()

    def test_cleanup_attempts_every_child_after_an_error(self):
        group = Processes(Path('.'))
        first, second, log = Mock(), Mock(), Mock()
        group.processes = [first, second]
        group.logs = [log]
        with patch.object(group, 'stop', side_effect=[OSError('Denied'), 0]) as stop, \
                self.assertLogs('tools.runtime', level='ERROR'), self.assertRaises(RuntimeError):
            group.close()
        self.assertEqual([call.args[0] for call in stop.call_args_list], [second, first])
        log.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
