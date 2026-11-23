"""Dashboard contract and failure-state tests independent of the live database."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from dashboard.model import (SnapshotReader, filtered_stations, station_status,
                             station_feature_collection, summary_metrics)


def fixture(events=10):
    return dict(schema_version=1, published_at=datetime.now(timezone.utc).isoformat(),
                coverage=dict(received_events=events, observed_from=None, observed_through=None),
                stations=[dict(ev_id=4, display_name='Harbour', station_address='1 Quay',
                               latitude=-33.84, longitude=151.21, operator_name='Network A',
                               operator_id=2, sa4_name='Sydney', price_per_kwh=.6,
                               price_event_ts='2026-09-01T00:00:00+10:00', active_sessions=2,
                               occupied_connectors=2, plugs_count=4, completed_sessions=3,
                               completed_energy_kwh=48.2)],
                sessions=[dict(ev_id=4, session_id='first', meter_curve=[]),
                          dict(ev_id=4, session_id='second', meter_curve=[])], daily_usage=[],
                future_extension={'keep': True})


class SnapshotReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.live = Path(self.tmp.name) / 'runtime.json'
        self.saved = Path(self.tmp.name) / 'saved.json'

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, data):
        path.write_text(json.dumps(data), encoding='utf-8')

    def test_wait_saved_then_live_without_rollback(self):
        reader = SnapshotReader(self.live, self.saved)
        self.assertEqual(reader.read()['state'], 'unavailable')
        self.write(self.saved, fixture(2))
        self.assertEqual(reader.read()['source'], 'saved')
        self.write(self.live, fixture(10))
        self.assertEqual(reader.read()['state'], 'fresh')
        self.live.unlink()
        result = reader.read()
        self.assertEqual((result['source'], result['state']), ('live', 'stale'))
        self.assertEqual(result['snapshot']['coverage']['received_events'], 10)
        self.assertEqual(result['source_path'], str(self.live))

    def test_invalid_live_retains_last_good_and_recovers(self):
        reader = SnapshotReader(self.live, self.saved)
        self.write(self.live, fixture(10))
        self.assertEqual(reader.read()['snapshot']['coverage']['received_events'], 10)
        self.live.write_text('{partially written', encoding='utf-8')
        failed = reader.read()
        self.assertEqual(failed['state'], 'invalid')
        self.assertEqual(failed['snapshot']['coverage']['received_events'], 10)
        self.write(self.live, fixture(11))
        self.assertEqual(reader.read()['snapshot']['coverage']['received_events'], 11)

    def test_schema_validation_and_initial_saved_fallback(self):
        reader = SnapshotReader(self.live, self.saved)
        invalid = fixture()
        invalid['sessions'] = {}
        self.write(self.live, invalid)
        self.assertEqual(reader.read()['state'], 'invalid')
        self.write(self.saved, fixture(2))
        self.assertEqual(reader.read()['state'], 'saved')
        invalid = fixture()
        invalid['schema_version'] = True
        self.write(self.saved, invalid)
        self.assertEqual(reader.read()['state'], 'invalid')

    def test_invalid_station_numbers_keep_last_good_data_and_recover(self):
        reader = SnapshotReader(self.live, None)
        self.write(self.live, fixture(10))
        self.assertEqual(reader.read()['state'], 'fresh')
        for key, value in (('price_per_kwh', 'bad-price'), ('power_kw', True),
                           ('completed_energy_kwh', float('inf')),
                           ('utilisation_pct', float('nan'))):
            with self.subTest(key=key, value=value):
                invalid = fixture(11)
                invalid['stations'][0][key] = value
                self.write(self.live, invalid)
                result = reader.read()
                self.assertEqual(result['state'], 'invalid')
                self.assertEqual(result['snapshot']['coverage']['received_events'], 10)
                self.assertEqual(result['snapshot']['stations'][0]['price_per_kwh'], .6)
        self.write(self.live, fixture(12))
        recovered = reader.read()
        self.assertEqual(recovered['state'], 'fresh')
        self.assertEqual(recovered['snapshot']['coverage']['received_events'], 12)

    def test_old_publication_and_immutable_response(self):
        data = fixture()
        data['published_at'] = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        self.write(self.live, data)
        reader = SnapshotReader(self.live, None, stale_after=5)
        result = reader.read()
        self.assertEqual(result['state'], 'stale')
        self.assertGreater(result['age_seconds'], 55)
        self.assertEqual(len(result['snapshot']['sessions']), 2)
        self.assertEqual(result['snapshot']['future_extension'], {'keep': True})
        result['snapshot']['stations'].clear()
        self.live.unlink()
        self.assertEqual(len(reader.read()['snapshot']['stations']), 1)


class StationModelTests(unittest.TestCase):
    def test_filter_ids_search_and_quality_states(self):
        data = fixture()
        second = dict(data['stations'][0], ev_id=2, display_name='Other', operator_name='Network B',
                      active_sessions=0, occupied_connectors=0, price_per_kwh=None,
                      price_event_ts=None, completed_sessions=0)
        attention = dict(second, ev_id=8, incomplete_sessions=1)
        data['stations'] += [attention, second]
        self.assertEqual([s['ev_id'] for s in filtered_stations(data)], [2, 4, 8])
        self.assertEqual([s['ev_id'] for s in filtered_stations(data, search='QUAY', operator='Network A')], [4])
        self.assertEqual([s['ev_id'] for s in filtered_stations(data, search='Sydney', status='unseen')], [2])
        self.assertEqual(station_status(attention), 'attention')
        self.assertEqual(station_status(dict(second, connector_conflict=True)), 'attention')
        self.assertEqual(station_status(dict(second, price_per_kwh=0)), 'idle')

    def test_null_unknown_metrics_and_zero_price(self):
        data = fixture()
        data['stations'].append(dict(ev_id=9, price_per_kwh=0, active_sessions=None,
                                     completed_sessions=None, completed_energy_kwh=None,
                                     unknown_metric='future'))
        metrics = summary_metrics(data)
        self.assertEqual(metrics, dict(stations=2, priced_stations=2, active_sessions=2,
                                       completed_sessions=3, completed_energy_kwh=48.2,
                                       received_events=10))
        data['coverage']['received_events'] = None
        self.assertEqual(summary_metrics(data)['received_events'], 0)

    def test_geojson_keeps_colocated_ids_and_exact_coordinates(self):
        station = fixture()['stations'][0]
        colocated = dict(station, ev_id=9)
        invalid = dict(station, ev_id=12, latitude=float('nan'))
        inputs = [station, colocated, invalid]
        original = deepcopy(inputs)
        collection = station_feature_collection(inputs, selected_id='9')
        self.assertEqual([f['id'] for f in collection['features']], [4, 9])
        for feature in collection['features']:
            self.assertEqual(feature['geometry']['coordinates'], [151.21, -33.84])
            self.assertEqual(feature['properties']['ev_id'], feature['id'])
        self.assertFalse(collection['features'][0]['properties']['selected'])
        self.assertTrue(collection['features'][1]['properties']['selected'])
        self.assertEqual(inputs[:2], original[:2])


if __name__ == '__main__':
    unittest.main()
