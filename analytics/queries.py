"""Query dashboard data in the process that owns the database connection."""
import json
import os
import tempfile
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def rows(db, sql, params=None):
    result = db.execute(sql, params or [])
    names = [c[0] for c in result.description]
    return [dict(zip(names, row)) for row in result.fetchall()]


def stations(db):
    return rows(db, '''SELECT m.*, a.usage_type, a.membership_required,
        p.website AS operator_website, p.contact_phone AS operator_phone,
        p.contact_email AS operator_email
        FROM analytics.station_metrics m
        LEFT JOIN core.dc_augmentation a USING (ev_id)
        LEFT JOIN core.operator_profile p ON p.profile_id=a.operator_profile_id
        ORDER BY m.ev_id''')


def session_curve(db, ev_id, session_id):
    return rows(db, '''SELECT event_ts,power_kw,energy_kwh AS cumulative_energy_kwh,soc_pct
        FROM core.meter_reading WHERE ev_id=? AND session_id=?
        ORDER BY event_ts,event_id''', [int(ev_id), session_id])


def history(db, ev_id):
    return rows(db, 'SELECT * FROM analytics.daily_usage WHERE ev_id=? ORDER BY completion_date', [int(ev_id)])


def snapshot(db):
    """Read all dashboard data between event writes so the values stay consistent."""
    current = stations(db)
    sessions = rows(db, "SELECT * FROM core.charging_session WHERE status<>'completed' ORDER BY ev_id,session_id")
    for session in sessions:
        session['meter_curve'] = session_curve(db, session['ev_id'], session['session_id'])
    plugs = rows(db, 'SELECT ev_id,plug_type FROM core.charger_plug ORDER BY ev_id,plug_type')
    by_station = {}
    for plug in plugs:
        by_station.setdefault(plug['ev_id'], []).append(plug['plug_type'])
    for station in current:
        station['plug_types'] = by_station.get(station['ev_id'], [])
    return dict(schema_version=1, published_at=datetime.now(timezone.utc).isoformat(),
                coverage=rows(db, 'SELECT * FROM analytics.received_window')[0],
                stations=current, sessions=sessions,
                daily_usage=rows(db, 'SELECT * FROM analytics.daily_usage ORDER BY ev_id,completion_date'))


def publish_snapshot(db, path):
    """Replace the JSON file only when its new content is complete.

    A reader sees either the complete previous or complete new JSON document.
    published_at is wall time; observed_through is simulation event time.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(snapshot(db), default=lambda v: v.isoformat(), allow_nan=False)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name+'.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers or virus scanners can briefly deny replacement.
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if sys.platform != 'win32' or attempt == 7:
                    raise
                time.sleep(min(0.01 * 2 ** attempt, 0.1))
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
