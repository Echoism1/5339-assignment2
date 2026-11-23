"""Exercise the actual Dash callbacks and real Leaflet popup contents."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dash import dcc
from dash.development.base_component import Component
import dash_leaflet as dl

from app import create_app


ROOT = Path(__file__).resolve().parents[1]


def walk(value):
    """Visit components through their actual child tree."""
    if isinstance(value, (list, tuple)):
        for child in value:
            yield from walk(child)
    elif isinstance(value, Component):
        yield value
        yield from walk(getattr(value, 'children', None))


def text(value):
    if isinstance(value, (list, tuple)):
        return ' '.join(text(child) for child in value)
    if isinstance(value, Component):
        return text(getattr(value, 'children', None))
    return '' if value is None else str(value)


def by_id(value, wanted):
    return next(component for component in walk(value) if getattr(component, 'id', None) == wanted)


class DashboardAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real = json.loads((ROOT / 'artifacts/dashboard.json').read_text(encoding='utf-8'))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(mode='saved', snapshot_path=Path(self.tmp.name) / 'live.json',
                              saved_path=ROOT / 'artifacts/dashboard.json')
        self.client = self.app.server.test_client()
        self.envelope = dict(snapshot=deepcopy(self.real), source='saved', state='saved',
                             message='Saved test snapshot.', age_seconds=0, source_path='artifacts/dashboard.json')

    def tearDown(self):
        self.tmp.cleanup()

    def callback(self, name):
        return next(item['callback'].__wrapped__ for item in self.app.callback_map.values()
                    if item['callback'].__wrapped__.__name__ == name)

    def test_flask_routes_and_marker_click_callback(self):
        health = self.client.get('/healthz')
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json, dict(status='ok', component='member4-dashboard', mode='saved'))
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.get('/_dash-layout').status_code, 200)
        self.assertEqual(self.client.get('/_dash-dependencies').status_code, 200)
        output = next(key for key, item in self.app.callback_map.items()
                      if item['callback'].__wrapped__.__name__ == 'select_station')
        response = self.client.post('/_dash-update-component', json={
            'output': output,
            'outputs': [{'id': 'selected-station', 'property': 'data'},
                        {'id': 'station-picker', 'property': 'value'}],
            'changedPropIds': ['station-layer.clickData'],
            'inputs': [{'id': 'station-layer', 'property': 'clickData',
                        'value': {'properties': {'ev_id': 100}}},
                       {'id': 'station-picker', 'property': 'value', 'value': None}],
            'state': [{'id': 'selected-station', 'property': 'data', 'value': None}],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['response']['selected-station']['data'], 100)
        self.assertEqual(response.json['response']['station-picker']['value'], 100)

    def test_saved_overview_uses_stored_metrics_and_map_identities(self):
        result = self.callback('overview')(self.envelope, 100, '', 'all', 'all')
        self.assertEqual(result[0], 'SAVED SNAPSHOT')
        self.assertEqual(text(by_id(result[4], 'active-total')), '9')
        self.assertEqual(text(by_id(result[4], 'priced-total')), '433 / 433')
        self.assertEqual(text(by_id(result[4], 'energy-total')), '1,674.91')
        self.assertEqual(text(by_id(result[4], 'completed-total')), '62')
        self.assertEqual(result[5], '433 of 433 DC records shown')
        self.assertEqual(len(result[6]['features']), 433)
        feature = next(f for f in result[6]['features'] if f['properties']['ev_id'] == 100)
        self.assertTrue(feature['properties']['selected'])
        self.assertEqual(feature['geometry']['coordinates'], [150.8988605, -33.8505352])
        self.assertIn('received events', result[2])
        self.assertIn('Latest event time', result[2])

    def test_marker_details_contain_real_popup_curve_and_required_metadata(self):
        station = next(s for s in self.real['stations'] if s['ev_id'] == 100)
        session = next(s for s in self.real['sessions'] if s['ev_id'] == 100)
        result = self.callback('selected_details')(self.envelope, 100, session['session_id'])
        self.assertEqual(len(result[5]), 1)
        popup = result[5][0]
        self.assertIsInstance(popup, dl.Popup)
        self.assertEqual(popup.position, [station['latitude'], station['longitude']])
        popup_text = text(popup)
        for expected in (station['display_name'], station['station_address'], station['operator_name'],
                         'Plugs: Not recorded', 'A$0.660 / kWh', 'Current: 1 active',
                         'History: 1 completed, 21.61 kWh', '24 meter readings'):
            self.assertIn(expected, popup_text)
        # Looking inside Popup itself prevents a sidebar-only curve from passing.
        graph = by_id(popup, 'popup-curve')
        self.assertIsInstance(graph, dcc.Graph)
        traces = {trace.name: trace for trace in graph.figure.data}
        self.assertEqual(len(traces['Power (kW)'].x), 24)
        self.assertEqual(list(traces['Power (kW)'].y), [p['power_kw'] for p in session['meter_curve']])
        self.assertEqual(list(traces['Cumulative energy (kWh)'].y),
                         [p['cumulative_energy_kwh'] for p in session['meter_curve']])
        self.assertTrue(all(str(x).endswith('+10:00') for x in traces['Power (kW)'].x))
        self.assertEqual(result[3].data[0].value, 1)
        history = [r for r in self.real['daily_usage'] if r['ev_id'] == 100]
        self.assertEqual(list(result[4].data[0].y), [r['energy_kwh'] for r in history])

    def test_open_popup_curve_grows_and_session_completion_clears_active_curve(self):
        session = next(s for s in self.envelope['snapshot']['sessions'] if s['ev_id'] == 100)
        sid = session['session_id']
        full_curve = deepcopy(session['meter_curve'])
        session['meter_curve'] = full_curve[:2]
        before = self.callback('selected_details')(self.envelope, 100, sid)
        self.assertEqual(len(by_id(before[5][0], 'popup-curve').figure.data[0].x), 2)
        session['meter_curve'] = full_curve
        after = self.callback('selected_details')(self.envelope, 100, sid)
        self.assertEqual(len(by_id(after[5][0], 'popup-curve').figure.data[0].x), 24)
        self.envelope['snapshot']['sessions'] = [s for s in self.envelope['snapshot']['sessions'] if s['ev_id'] != 100]
        station = next(s for s in self.envelope['snapshot']['stations'] if s['ev_id'] == 100)
        station.update(active_sessions=0, occupied_connectors=0, completed_sessions=2)
        options, selected = self.callback('session_choices')(self.envelope, 100, sid)
        self.assertEqual((options, selected), ([], None))
        ended = self.callback('selected_details')(self.envelope, 100, sid)
        graph = by_id(ended[5][0], 'popup-curve')
        self.assertEqual(len(graph.figure.data), 0)
        self.assertIn('No active session', graph.figure.layout.annotations[0].text)
        self.assertIn('History: 2 completed', text(ended[5][0]))
        self.assertEqual(ended[3].data[0].value, 0)

    def test_existing_access_and_operator_details_with_missing_values(self):
        result = self.callback('selected_details')(self.envelope, 103, None)
        sidebar, popup = result[0], result[5][0]
        for component in (sidebar, popup):
            self.assertIn('Usage type Public - Membership Required', text(component))
            self.assertIn('Membership Required', text(component))
            link = next(c for c in walk(component) if getattr(c, 'href', None))
            self.assertEqual(link.href, 'https://evie.com.au/find-a-charger/')
        self.assertIn('Operator email info@evie.com.au', text(sidebar))
        self.assertNotIn('Operator email', text(popup))
        phone = self.callback('selected_details')(self.envelope, 67, None)
        self.assertIn('Operator phone 13 14 04', text(phone[0]))
        self.assertNotIn('Operator phone', text(phone[5][0]))
        station = next(s for s in self.envelope['snapshot']['stations'] if s['ev_id'] == 103)
        station['membership_required'] = False
        self.assertIn('Membership Not required', text(self.callback('selected_details')(self.envelope, 103, None)[5]))
        for key in ('usage_type', 'membership_required', 'operator_website', 'operator_phone', 'operator_email'):
            station.pop(key)
        empty = self.callback('selected_details')(self.envelope, 103, None)
        for label in ('Usage type', 'Membership', 'Operator website', 'Operator phone', 'Operator email'):
            self.assertNotIn(label, text(empty[0]))
            self.assertNotIn(label, text(empty[5]))

    def test_multiple_sessions_keep_selection_and_missing_meter_is_explicit(self):
        original = next(s for s in self.envelope['snapshot']['sessions'] if s['ev_id'] == 100)
        another = deepcopy(original)
        another.update(session_id='second-same-station', connector_id=1, meter_curve=[])
        self.envelope['snapshot']['sessions'].append(another)
        choices, selected = self.callback('session_choices')(self.envelope, 100, another['session_id'])
        self.assertEqual(len(choices), 2)
        self.assertEqual(selected, another['session_id'])
        result = self.callback('selected_details')(self.envelope, 100, selected)
        graph = by_id(result[5][0], 'popup-curve')
        self.assertIn('waiting for the first meter event', graph.figure.layout.annotations[0].text)
        self.assertIn('Connector 1', text(result[5][0]))
        self.assertIn('Session / connector selection', text(result[5][0]))

    def test_unavailable_unknown_station_and_quality_warning(self):
        empty = dict(self.envelope, snapshot=None, source='unavailable', state='unavailable')
        overview = self.callback('overview')(empty, None, '', 'all', 'all')
        self.assertEqual(overview[0], 'WAITING FOR DATA')
        self.assertEqual(overview[6]['features'], [])
        self.assertEqual(text(by_id(overview[4], 'active-total')), '0')
        self.assertEqual(self.callback('session_choices')(empty, 100, None), ([], None))
        self.assertEqual(self.callback('selected_details')(empty, 999999, None)[5], [])
        station = next(s for s in self.envelope['snapshot']['stations'] if s['ev_id'] == 100)
        station.update(connector_conflict=True, incomplete_sessions=1, price_per_kwh=None, utilisation_pct=None)
        result = self.callback('selected_details')(self.envelope, 100, None)
        self.assertIn('Conflicting connector occupancy', text(result[5][0]))
        self.assertIn('1 incomplete session(s)', text(result[5][0]))
        self.assertIn('—', text(result[5][0]))
        attention = self.callback('overview')(self.envelope, 100, '', 'all', 'attention')
        self.assertTrue(any(f['properties']['ev_id'] == 100 for f in attention[6]['features']))

    def test_dashboard_can_render_without_opening_duckdb(self):
        with patch('duckdb.connect', side_effect=AssertionError('dashboard opened live database')):
            app = create_app(mode='saved', saved_path=ROOT / 'artifacts/dashboard.json')
            self.assertEqual(app.server.test_client().get('/_dash-layout').status_code, 200)
            callbacks = {item['callback'].__wrapped__.__name__: item['callback'].__wrapped__
                         for item in app.callback_map.values()}
            envelope = callbacks['read_snapshot'](0, 0)
            self.assertEqual(envelope['source'], 'saved')
            self.assertEqual(len(callbacks['overview'](envelope, None, '', 'all', 'all')[6]['features']), 433)
            self.assertIsInstance(callbacks['selected_details'](envelope, 100, None)[5][0], dl.Popup)


if __name__ == '__main__':
    unittest.main()
