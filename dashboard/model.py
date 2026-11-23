"""Read complete JSON data files without opening the subscriber database."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError('published_at must be an ISO timestamp')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('published_at must include a time zone')
    return result


def _validate(data):
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise ValueError('expected snapshot schema_version 1')
    _timestamp(data.get('published_at'))
    if not isinstance(data.get('coverage'), dict):
        raise ValueError('coverage must be an object')
    for key in ('stations', 'sessions', 'daily_usage'):
        if not isinstance(data.get(key), list) or any(not isinstance(row, dict) for row in data[key]):
            raise ValueError(f'{key} must be a list of objects')
    for row in data['stations']:
        if type(row.get('ev_id')) is not int:
            raise ValueError('station ev_id must be an integer')
        for key in ('price_per_kwh', 'power_kw', 'latitude', 'longitude', 'plugs_count',
                    'active_sessions', 'occupied_connectors', 'completed_sessions',
                    'incomplete_sessions', 'completed_energy_kwh', 'completed_cost_aud',
                    'utilisation_pct'):
            value = row.get(key)
            if value is not None and _number(value, None) is None:
                raise ValueError(f'station {key} must be a finite number or null')
    return data


class SnapshotReader:
    """Use live data and keep the last valid copy if a file cannot be read.

    A saved development snapshot may be used only before live data has loaded.
    No database writes, event generation, or data repairs occur here.
    """
    def __init__(self, live_path: Path, saved_path: Path | None, stale_after: float = 5):
        self.live_path = Path(live_path)
        self.saved_path = Path(saved_path) if saved_path is not None else None
        if not math.isfinite(stale_after) or stale_after <= 0:
            raise ValueError('stale_after must be positive and finite')
        self.stale_after = stale_after
        self._last_live = None

    @staticmethod
    def _load(path):
        with path.open(encoding='utf-8') as stream:
            return _validate(json.load(stream))

    def _envelope(self, snapshot, source, state, message, path):
        age = None
        if snapshot is not None:
            age = max(0.0, (datetime.now(timezone.utc) - _timestamp(snapshot['published_at'])).total_seconds())
        return dict(snapshot=deepcopy(snapshot), source=source, state=state,
                    message=message, age_seconds=age, source_path=str(path))

    def read(self):
        """Return the data and a status showing whether it is available and up to date."""
        error = None
        try:
            data = self._load(self.live_path)
        except (OSError, UnicodeError, ValueError, TypeError, OverflowError) as exc:
            error = exc
        else:
            self._last_live = data
            result = self._envelope(data, 'live', 'fresh', 'Live data loaded.', self.live_path)
            if result['age_seconds'] > self.stale_after:
                result.update(state='stale', message='The live data file has not been updated recently.')
            return result

        missing = isinstance(error, FileNotFoundError)
        if self._last_live is not None:
            return self._envelope(self._last_live, 'live', 'stale' if missing else 'invalid',
                                  'The live data file is missing. Showing the last valid data.' if missing else
                                  'Cannot read the live data file. Showing the last valid data.',
                                  self.live_path)
        if self.saved_path is not None:
            try:
                saved = self._load(self.saved_path)
            except (OSError, UnicodeError, ValueError, TypeError, OverflowError):
                pass
            else:
                return self._envelope(saved, 'saved', 'saved',
                                      'Showing saved data. Live data has not loaded yet.', self.saved_path)
        return self._envelope(None, 'unavailable', 'unavailable' if missing else 'invalid',
                              'Waiting for the first live data file.' if missing else
                              'Cannot read the data file. No valid data is available.', self.live_path)


def _number(value, default=0):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else default


def station_status(station):
    """Return attention, active, idle, or unseen from received state only."""
    if (station.get('connector_conflict') is True or station.get('inconsistent') is True
            or _number(station.get('incomplete_sessions')) > 0):
        return 'attention'
    if _number(station.get('active_sessions')) > 0 or _number(station.get('occupied_connectors')) > 0:
        return 'active'
    if (station.get('price_event_ts') or _number(station.get('price_per_kwh'), None) is not None
            or _number(station.get('completed_sessions')) > 0):
        return 'idle'
    return 'unseen'


def filtered_stations(snapshot, search='', operator='all', status='all'):
    """Filter station metadata without changing identity or source coordinates."""
    search = str(search or '').strip().casefold()
    result = []
    for station in snapshot.get('stations', []):
        if operator != 'all' and str(operator) not in (str(station.get('operator_name')), str(station.get('operator_id'))):
            continue
        if status != 'all' and station_status(station) != status:
            continue
        searchable = ' '.join(str(station.get(k) or '') for k in
                              ('display_name', 'station_address', 'ev_id', 'sa4_name')).casefold()
        if search and search not in searchable:
            continue
        result.append(station)
    return sorted(result, key=lambda s: s['ev_id'])


def summary_metrics(snapshot):
    """Summarise stored station metrics, not cumulative meter readings."""
    stations = snapshot.get('stations', [])
    return dict(stations=len(stations),
                priced_stations=sum(_number(s.get('price_per_kwh'), None) is not None for s in stations),
                active_sessions=sum(_number(s.get('active_sessions')) for s in stations),
                completed_sessions=sum(_number(s.get('completed_sessions')) for s in stations),
                completed_energy_kwh=sum(_number(s.get('completed_energy_kwh')) for s in stations),
                received_events=_number(snapshot.get('coverage', {}).get('received_events')))


def station_feature_collection(stations, selected_id=None):
    """Create one map marker per station ID, even when stations share coordinates."""
    colors = {'active': '#16a5a2', 'idle': '#3179bb', 'unseen': '#8995a3', 'attention': '#e09a31'}
    features = []
    for station in sorted(stations, key=lambda s: s['ev_id']):
        lat, lon = _number(station.get('latitude'), None), _number(station.get('longitude'), None)
        if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            continue
        status = station_status(station)
        selected = selected_id is not None and str(station['ev_id']) == str(selected_id)
        price = _number(station.get('price_per_kwh'), None)
        occupied, plugs = _number(station.get('occupied_connectors'), None), _number(station.get('plugs_count'), None)
        occupancy = (f'{occupied:g} / {plugs:g} occupied' if occupied is not None and plugs is not None
                     else 'Occupancy unavailable')
        if status == 'unseen':
            occupancy = 'No event received'
        elif status == 'attention':
            occupancy += ' · check missing or conflicting session events'
        props = dict(ev_id=station['ev_id'],
                     label=station.get('display_name') or station.get('station_address') or f"Charger {station['ev_id']}",
                     operator=station.get('operator_name') or 'Unknown operator',
                     price_label=f'A${price:.3f} / kWh' if price is not None else 'Price not received',
                     occupancy_label=occupancy, status=status, color=colors[status],
                     radius=11 if selected else (8 if status == 'active' else 6), selected=selected)
        features.append(dict(type='Feature', id=station['ev_id'], properties=props,
                             geometry=dict(type='Point', coordinates=[station['longitude'], station['latitude']])))
    return dict(type='FeatureCollection', features=features)
