"""Contract, persistence and poison-message checks against the real A1 DB copy."""
import base64
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import duckdb

from ingestion.storage import ROOT, Store
from ingestion.validation import InvalidEvent, canonical, decode


def signed(event):
    event = {k: v for k, v in event.items() if k != 'event_id'}
    event['event_id'] = hashlib.sha256(canonical(event).encode()).hexdigest()
    return canonical(event).encode()


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events = [json.loads(line) for line in (ROOT / 'data/window.jsonl').read_text(encoding='utf-8').splitlines()]
        cls.stations = {'2': 4}

    def test_every_producer_payload(self):
        import csv
        with (ROOT / 'data/stations_input.csv').open(encoding='utf-8') as stream:
            stations = {r['station_id']: int(r['connectors']) for r in csv.DictReader(stream)}
        for event in self.events:
            self.assertEqual(decode(canonical(event).encode(), stations), event)

    def test_invalid_wire_messages(self):
        invalid = [b'\xff', b'{', b'[]', b'{"type":1}', b'{"type":"meter","type":"meter"}',
                   b'{"type":NaN}', b'{"type":Infinity}']
        original = self.events[0]
        for key, value in [('station_id', '999999'), ('station_id', 2), ('ts', '2026-09-01'),
                           ('type', 'unknown'), ('price_per_kwh', True), ('price_per_kwh', -1),
                           ('price_per_kwh', 10 ** 1000), ('extra', 1)]:
            invalid.append(signed(dict(original, **{key: value})))
        tampered = dict(original, price_per_kwh=99)
        invalid.append(canonical(tampered).encode())
        for payload in invalid:
            with self.subTest(payload=payload[:100]), self.assertRaises(InvalidEvent):
                decode(payload, self.stations)

    def test_connector_and_soc(self):
        meter = next(e for e in self.events if e['type'] == 'meter')
        stations = {meter['station_id']: 100}
        for change in ({'connector_id': 0}, {'connector_id': True}, {'soc_pct': 101}):
            with self.assertRaises(InvalidEvent):
                decode(signed(dict(meter, **change)), stations)

    def test_negative_wholesale_allowed(self):
        self.assertEqual(decode(signed(dict(self.events[0], wholesale_per_kwh=-0.1)), self.stations)['wholesale_per_kwh'], -0.1)

    def test_escaped_invalid_unicode_is_quarantinable(self):
        event = dict(next(e for e in self.events if e['type'] == 'session_start'))
        event['vehicle_model'] = '\ud800'
        event['event_id'] = '0' * 64
        payload = json.dumps(event, ensure_ascii=True).encode('utf-8')
        with self.assertRaisesRegex(InvalidEvent, 'invalid Unicode'):
            decode(payload, {event['station_id']: 100})

    def test_unplug_cannot_precede_charge_completion(self):
        event = next(e for e in self.events if e['type'] == 'session_end')
        bad = dict(event, charge_end_ts='2099-01-01T00:00:00+10:00')
        with self.assertRaisesRegex(InvalidEvent, 'completion is after unplugging'):
            decode(signed(bad), {event['station_id']: 100})


class PersistenceTests(unittest.TestCase):
    def test_durable_dedup_quarantine_and_fk(self):
        # Supply an A1 snapshot independently, not the production DB while it is open.
        import os
        source = os.environ.get('A1_TEST_DB')
        if not source:
            self.skipTest('set A1_TEST_DB to a closed A1 database')
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'test.duckdb'
            shutil.copy2(source, target)
            payload = (ROOT / 'data/window.jsonl').read_bytes().splitlines()[0]
            with Store(target) as store:
                self.assertEqual(store.receive('test/events', payload), 'stored')
                self.assertEqual(store.receive('test/events', payload), 'duplicate')
                self.assertEqual(store.receive('test/events', b'\xff'), 'rejected')
                self.assertEqual(store.receive('test/events', payload, retained=True), 'rejected')
                self.assertEqual(store.receive('test/events', payload, qos=0), 'rejected')
                source_events = (json.loads(line) for line in (ROOT / 'data/window.jsonl').read_text(encoding='utf-8').splitlines())
                bad_text = dict(next(e for e in source_events if e['type'] == 'session_start'),
                                vehicle_model='\ud800', event_id='0' * 64)
                self.assertEqual(store.receive('test/events', json.dumps(bad_text).encode()), 'rejected')
                raw = store.db.execute('select payload_base64 from core.mqtt_rejection where reason like ?',['JSON decoding:%']).fetchone()[0]
                self.assertEqual(base64.b64decode(raw), b'\xff')
                with self.assertRaises(Exception):
                    store.db.execute("insert into core.mqtt_event(event_id,ev_id,event_type,event_ts,topic,payload) values ('bad',999999,'meter',current_timestamp,'x','{}')")
            with Store(target) as reopened:
                self.assertEqual(reopened.receive('test/events', payload), 'duplicate')
                self.assertEqual(reopened.db.execute('select count(*) from core.mqtt_event').fetchone()[0], 1)
                self.assertEqual(reopened.db.execute('select count(*) from core.mqtt_rejection').fetchone()[0], 4)


class TimestampQueryTests(unittest.TestCase):
    def test_timezone_aware_timestamp_can_be_fetched(self):
        with duckdb.connect(':memory:') as connection:
            value = connection.execute("SELECT TIMESTAMPTZ '2026-09-01T00:00:00+10:00'").fetchone()[0]
        self.assertIsNotNone(value.utcoffset())


if __name__ == '__main__':
    unittest.main()
