"""Contract, persistence and poison-message checks against the real A1 DB copy."""
import base64
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

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
                raw = store.db.execute('select payload_base64 from core.mqtt_rejection where reason like ?',['JSON decoding:%']).fetchone()[0]
                self.assertEqual(base64.b64decode(raw), b'\xff')
                with self.assertRaises(Exception):
                    store.db.execute("insert into core.mqtt_event(event_id,ev_id,event_type,event_ts,topic,payload) values ('bad',999999,'meter',current_timestamp,'x','{}')")
            with Store(target) as reopened:
                self.assertEqual(reopened.receive('test/events', payload), 'duplicate')
                self.assertEqual(reopened.db.execute('select count(*) from core.mqtt_event').fetchone()[0], 1)
                self.assertEqual(reopened.db.execute('select count(*) from core.mqtt_rejection').fetchone()[0], 3)


if __name__ == '__main__':
    unittest.main()
