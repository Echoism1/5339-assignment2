"""Show station locations and charging curves from saved or live dashboard data."""
import argparse
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from dash import Dash, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate
import dash_leaflet as dl
from dash_extensions.javascript import Namespace
from flask import jsonify
import plotly.graph_objects as go
import pytz

from analytics.charts import current_state_figure, history_figure, live_curve_figure
from dashboard.model import (SnapshotReader, filtered_stations, station_feature_collection,
                             station_status, summary_metrics)

ROOT = Path(__file__).resolve().parent
SYDNEY = pytz.timezone('Australia/Sydney')
EMPTY = dict(stations=[], sessions=[], daily_usage=[], coverage={})
JS = Namespace('evDashboard', 'map')
GRAPH_CONFIG = dict(displayModeBar=False, responsive=True)


def local_time(value, compact=False):
    if not value:
        return 'Not received'
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(SYDNEY)
        return dt.strftime('%H:%M:%S' if compact else '%d %b %Y, %H:%M:%S %Z')
    except (TypeError, ValueError):
        return str(value)


def number(value, precision=0, prefix='', suffix=''):
    if value is None:
        return '—'
    return f'{prefix}{value:,.{precision}f}{suffix}'


def style_figure(fig, height=265):
    fig.update_layout(height=height, template='plotly_white',
                      font=dict(family='Arial, sans-serif', size=11, color='#506178'),
                      margin=dict(l=46, r=46, t=37, b=50),
                      paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                      title_font=dict(size=13, color='#1c3047'),
                      legend=dict(orientation='h', y=1.08, x=0, font_size=10),
                      uirevision=fig.layout.uirevision or 'keep-user-view', hovermode='x unified')
    fig.update_xaxes(gridcolor='#edf1f4', zeroline=False)
    fig.update_yaxes(gridcolor='#edf1f4', zeroline=False)
    return fig


def empty_figure(message='Select a station to see its received events.', height=255):
    fig = go.Figure()
    fig.add_annotation(text=message, x=.5, y=.5, xref='paper', yref='paper',
                       showarrow=False, font=dict(size=12, color='#7c8a99'))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return style_figure(fig, height)


def curve_figure(session, height=255):
    if session is None:
        return empty_figure('No active session in the available data.', height)
    if not session.get('meter_curve'):
        return empty_figure('Session started · waiting for the first meter event.', height)
    display = deepcopy(session)
    for point in display['meter_curve']:
        point['event_ts'] = datetime.fromisoformat(point['event_ts']).astimezone(SYDNEY).isoformat()
    fig = live_curve_figure(display)
    fig.update_layout(title=None, uirevision=f"{session['ev_id']}:{session['session_id']}")
    fig.update_xaxes(title_text='Event time · Australia/Sydney', tickformat='%H:%M')
    fig.update_traces(line_color='#0b9c91', selector=dict(name='Power (kW)'))
    fig.update_traces(line_color='#3e79c3', selector=dict(name='Cumulative energy (kWh)'))
    return style_figure(fig, height)


def metric(label, value, note='', accent=False, id=None):
    return html.Div([html.Div(label, className='metric-label'),
                     html.Div(value, className='metric-value', **({'id':id} if id else {})),
                     html.Div(note, className='metric-note')],
                    className='metric-card' + (' accent' if accent else ''))


def quality_warning(station, sessions):
    issues = []
    if station.get('connector_conflict'):
        issues.append('Conflicting connector occupancy')
    if station.get('incomplete_sessions'):
        issues.append(f"{station['incomplete_sessions']} incomplete session(s)")
    if any(s.get('inconsistent') for s in sessions):
        issues.append('Inconsistent session history')
    return html.Div(' · '.join(issues), className='quality-warning') if issues else None


def extra_station_details(station, contacts=False):
    fields = [('Usage type', station.get('usage_type'))]
    membership = station.get('membership_required')
    if membership is not None:
        fields.append(('Membership', 'Required' if membership else 'Not required'))
    website = station.get('operator_website')
    if website:
        link = (html.A('Visit website', href=website, target='_blank', rel='noopener noreferrer')
                if website.startswith(('https://', 'http://')) else website)
        fields.append(('Operator website', link))
    if contacts:
        fields.extend([('Operator phone', station.get('operator_phone')),
                       ('Operator email', station.get('operator_email'))])
    return [html.Div([html.Span(label), html.Strong(value)], className='detail-row')
            for label, value in fields if value is not None and value != '']


def station_details(station, sessions):
    plugs = ', '.join(station.get('plug_types', [])) or 'Not recorded'
    return html.Div([
        html.Div([html.Span(station.get('operator_name') or 'Unknown operator', className='operator'),
                  html.Span('DC · A1 #' + str(station['ev_id']), className='station-id')], className='station-meta'),
        html.H3(station.get('display_name') or f"Charger {station['ev_id']}"),
        html.P(station.get('station_address') or 'Address not recorded', className='address'),
        html.Div([html.Span('Plug types'), html.Strong(plugs)], className='detail-row'),
        html.Div([html.Span('Rated power / connector'), html.Strong(number(station.get('power_kw'), suffix=' kW'))], className='detail-row'),
        html.Div([html.Span('Region'), html.Strong(station.get('sa4_name') or 'Not recorded')], className='detail-row'),
        *extra_station_details(station, contacts=True),
        html.Div([html.Span('Latest received price'),
                  html.Strong(number(station.get('price_per_kwh'), 3, 'A$', ' / kWh'))], className='detail-row price-row'),
        html.Div('Price event: ' + local_time(station.get('price_event_ts')), className='subtle'),
        html.Div([
            metric('ACTIVE NOW', number(station.get('active_sessions')), 'Based on received events'),
            metric('COMPLETED', number(station.get('completed_sessions')), 'Stored completed sessions'),
            metric('ENERGY', number(station.get('completed_energy_kwh'), 2), 'kWh · completed sessions'),
            metric('UTILISATION', number(station.get('utilisation_pct'), 1, suffix='%'), 'Over the received time period'),
        ], className='station-metrics'),
        quality_warning(station, sessions),
    ])


def popup_contents(station, sessions, session):
    """Build the map popup with station details and an updating charging curve."""
    title = station.get('display_name') or f"Charger {station['ev_id']}"
    return html.Div([
        html.Div('CHARGER DETAILS', className='eyebrow'),
        html.H3(title, className='popup-title'),
        html.Div(f"A1 #{station['ev_id']} · {station.get('operator_name') or 'Unknown operator'} · DC", className='subtle'),
        html.P(station.get('station_address') or 'Address not recorded', className='popup-address'),
        html.Div('Plugs: ' + (', '.join(station.get('plug_types', [])) or 'Not recorded'), className='subtle'),
        html.Div('Rated power: ' + number(station.get('power_kw'), suffix=' kW per connector'), className='subtle'),
        *extra_station_details(station),
        html.Div([
            html.Span(number(station.get('price_per_kwh'), 3, 'A$', ' / kWh'), className='popup-price'),
            html.Span(f"{station.get('occupied_connectors', 0)} / {station.get('plugs_count', '—')} occupied", className='occupancy-pill'),
        ], className='popup-price-row'),
        html.Div(f"Current: {station.get('active_sessions', 0)} active · History: {station.get('completed_sessions', 0)} completed, "
                 + number(station.get('completed_energy_kwh'), 2, suffix=' kWh'), className='popup-history'),
        quality_warning(station, sessions),
        html.Div('LIVE SESSION PROGRESS' if session else 'SESSION STATE', className='eyebrow chart-label'),
        html.Div(f"Connector {session['connector_id']} · {session.get('vehicle_model') or 'Vehicle not recorded'} · "
                 f"{len(session.get('meter_curve', []))} meter readings" if session else
                 'No active session in the available data.', className='subtle', id='popup-session-state'),
        dcc.Graph(id='popup-curve', figure=curve_figure(session, 238), config=GRAPH_CONFIG, style={'height':'238px'}),
        html.Div('Last meter: ' + local_time(session.get('latest_meter_ts')) if session else
                 'Completed history remains available in the station panel.', className='subtle'),
        html.Div('Session / connector selection is available in the station panel.' if len(sessions) > 1 else
                 'The connector stays occupied until an unplugging event arrives.', className='popup-footnote'),
    ], className='popup-card')


def create_app(mode='auto', snapshot_path=ROOT/'runtime/dashboard.json',
               saved_path=ROOT/'artifacts/dashboard.json', interval=1000, stale_after=5):
    live_path = Path(snapshot_path)
    # Saved mode reads the saved snapshot even when a live file exists.
    reader = SnapshotReader(live_path if mode != 'saved' else ROOT/'runtime/.saved-mode-unused',
                            Path(saved_path) if mode != 'live' else None, stale_after)
    initial = reader.read()
    seed = initial.get('snapshot') or EMPTY
    app = Dash(__name__, assets_folder=str(ROOT/'assets'), title='EV Stream · NSW',
               suppress_callback_exceptions=True, update_title=None)
    app.layout = html.Div([
        dcc.Store(id='snapshot-store', data=initial),
        dcc.Store(id='selected-station', data=None),
        dcc.Store(id='popup-open', data=False),
        dcc.Interval(id='refresh-timer', interval=interval),
        html.Header([
            html.Div([html.Div('EV', className='brand-mark'), html.Div([
                html.Div([html.Strong('EV STREAM'), html.Span(' / NSW', className='brand-light')], className='brand-name'),
                html.Div('Charging activity from received events', className='brand-subtitle')])], className='brand'),
            html.Div([html.Span('COMP5339', className='course-tag'), html.Div(id='source-badge', className='source-badge')], className='header-right'),
        ], className='topbar'),
        html.Main([
            html.Div([html.Div([html.Div('OPERATIONS OVERVIEW', className='eyebrow'),
                                html.H1('NSW charging network'),
                                html.P('Explore charger locations, received prices and live session progress.', className='page-description')]),
                      html.Div([html.Span('Refresh', className='control-label'),
                                dcc.Checklist(id='auto-refresh', options=[{'label':'Auto · 1 second','value':'on'}], value=['on'], className='refresh-control'),
                                html.Button('Refresh now', id='refresh-now', className='button')], className='refresh-actions')], className='page-heading'),
            html.Div(id='data-notice', className='data-notice'),
            html.Div(id='network-metrics', className='network-metrics'),
            html.Div([
                html.Section([
                    html.Div([html.Div([html.H2('Charger map'), html.Span(id='station-count', className='subtle')]),
                              html.Button('Fit NSW', id='fit-map', className='button small')], className='panel-header'),
                    html.Div([
                        dcc.Input(id='search-stations', placeholder='Search address, region or station ID', type='search', debounce=True, className='search-input'),
                        dcc.Dropdown(id='operator-filter', options=[{'label':'All operators','value':'all'}] +
                                     [{'label':o,'value':o} for o in sorted({s.get('operator_name') for s in seed['stations'] if s.get('operator_name')})],
                                     value='all', clearable=False, className='operator-filter'),
                        dcc.Dropdown(id='status-filter', options=[{'label':a,'value':b} for a,b in
                                     [('All states','all'),('Active','active'),('Idle','idle'),('Awaiting events','unseen'),('Needs attention','attention')]],
                                     value='all', clearable=False, className='status-filter'),
                    ], className='map-filters'),
                    dl.Map([
                        dl.TileLayer(url='https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
                                     attribution='© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'),
                        dl.GeoJSON(id='station-layer', data=station_feature_collection(seed['stations']),
                                   pointToLayer=JS('pointToLayer'), onEachFeature=JS('onEachFeature'),
                                   options=dict(pane='markerPane'), zoomToBoundsOnClick=False),
                        dl.LayerGroup(id='popup-holder'),
                    ], id='charger-map', center=[-32.2,147.4], zoom=6, minZoom=4, maxZoom=18,
                       eventHandlers={'popupopen':JS('popupOpened'),'keydown':JS('mapKeydown')},
                       preferCanvas=False, fadeAnimation=False, closePopupOnClick=False, trackViewport=True, className='charger-map'),
                    html.Div([html.Span([html.I(className='legend-dot active'),'Active']),
                              html.Span([html.I(className='legend-dot idle'),'Idle']),
                              html.Span([html.I(className='legend-dot unseen'),'Awaiting events']),
                              html.Span([html.I(className='legend-dot attention'),'Needs attention']),
                              html.Span('Hover for price & occupancy · click for details', className='legend-help')], className='map-legend'),
                ], className='panel map-panel'),
                html.Aside([
                    html.Div([html.H2('Station explorer'), html.Span('Station IDs from Assignment 1', className='subtle')], className='panel-header'),
                    dcc.Dropdown(id='station-picker', options=[{'label':f"#{s['ev_id']} · {s['display_name']}", 'value':s['ev_id']} for s in seed['stations']],
                                 placeholder='Click a marker or choose a station', className='station-picker'),
                    html.Div(id='station-detail', children=html.Div([
                        html.Div('↗', className='empty-arrow'), html.H3('Pick a charger'),
                        html.P('Click any marker to open its details and live charging curve. You can also search by station ID above.')], className='empty-selection')),
                    html.Div(id='session-section', children=[
                        html.Div('SESSION / CONNECTOR', className='eyebrow'),
                        dcc.Dropdown(id='session-picker', options=[], value=None, clearable=False, placeholder='No active session'),
                        html.Div(id='session-note', className='subtle session-note'),
                        dcc.Graph(id='session-curve', figure=empty_figure(), config=GRAPH_CONFIG, style={'height':'255px'}),
                    ]),
                ], className='panel station-panel'),
            ], className='workspace-grid'),
            html.Div([
                html.Section([html.Div([html.H2('Current station state'),html.Span('Latest received values',className='subtle')],className='panel-header'),
                              dcc.Graph(id='current-chart',figure=empty_figure(),config=GRAPH_CONFIG,style={'height':'250px'})],className='panel'),
                html.Section([html.Div([html.H2('Completed session history'),html.Span('Stored events · Sydney completion date',className='subtle')],className='panel-header'),
                              dcc.Graph(id='history-chart',figure=empty_figure(),config=GRAPH_CONFIG,style={'height':'250px'})],className='panel'),
            ], className='history-grid'),
            html.Footer([html.Span('433 DC charger records · TUT19-AssignmentGroup-08'),
                         html.Span('Simulated readings and prices · received events only')]),
        ], className='page'),
    ], className='app-shell')

    @app.callback(Output('refresh-timer','disabled'), Input('auto-refresh','value'))
    def refresh_control(value):
        return 'on' not in (value or [])

    @app.callback(Output('snapshot-store','data'), Input('refresh-timer','n_intervals'), Input('refresh-now','n_clicks'),
                  State('snapshot-store','data'))
    def read_snapshot(_, __, previous=None):
        result = reader.read()
        # Age alone has no visible effect until the freshness state changes.
        # Avoid reopening identical popup portals on every saved/stale poll.
        visible = ('snapshot', 'source', 'state', 'message', 'source_path')
        if previous and all(result.get(key) == previous.get(key) for key in visible):
            return no_update
        return result

    @app.callback(Output('selected-station','data'), Output('station-picker','value'),
                  Input('station-layer','clickData'), Input('station-picker','value'),
                  State('selected-station','data'), prevent_initial_call=True)
    def select_station(feature, value, current):
        if ctx.triggered_id == 'station-layer' and feature:
            props = feature.get('properties') or feature.get('feature',{}).get('properties',{})
            value = props.get('ev_id')
        if value is None:
            return None, None
        if str(value) == str(current):
            raise PreventUpdate
        return int(value), int(value)

    @app.callback(Output('popup-open','data'), Input('station-layer','n_clicks'),
                  Input('station-picker','value'), Input('charger-map','clickData'),
                  prevent_initial_call=True)
    def popup_visibility(_, selected, map_click):
        if ctx.triggered_id == 'charger-map':
            if map_click and map_click.get('action') == 'dismiss_popup':
                return False
            raise PreventUpdate
        return selected is not None or ctx.triggered_id == 'station-layer'

    @app.callback(Output('session-picker','options'), Output('session-picker','value'),
                  Input('snapshot-store','data'), Input('selected-station','data'), State('session-picker','value'))
    def session_choices(envelope, selected, previous):
        snapshot = envelope.get('snapshot') or EMPTY
        sessions = [s for s in snapshot['sessions'] if s['ev_id'] == selected]
        choices = [{'label':f"Connector {s['connector_id']} · {s['status']} · {s['session_id']}", 'value':s['session_id']} for s in sessions]
        ids = [x['value'] for x in choices]
        return choices, previous if previous in ids else (ids[0] if ids else None)

    @app.callback(Output('source-badge','children'), Output('source-badge','className'),
                  Output('data-notice','children'), Output('data-notice','className'),
                  Output('network-metrics','children'), Output('station-count','children'), Output('station-layer','data'),
                  Output('operator-filter','options'), Output('station-picker','options'),
                  Input('snapshot-store','data'), Input('selected-station','data'),
                  Input('search-stations','value'), Input('operator-filter','value'), Input('status-filter','value'),
                  State('station-layer','data'))
    def overview(envelope, selected, search, operator, status, current_features=None):
        snapshot = envelope.get('snapshot') or EMPTY
        totals = summary_metrics(snapshot)
        subset = filtered_stations(snapshot, search, operator, status)
        state = envelope['state']
        labels = dict(fresh='LIVE SNAPSHOT', stale='STALE SNAPSHOT', saved='SAVED SNAPSHOT',
                      unavailable='WAITING FOR DATA', invalid='SNAPSHOT ERROR')
        through = snapshot.get('coverage',{}).get('observed_through')
        message = envelope['message'] + ' ' + (
            f"{totals['received_events']:,} received events · Latest event time {local_time(through)}. "
            if through else 'No events have arrived yet. ')
        if state == 'fresh':
            message += 'The data file is up to date. This alone does not confirm the MQTT connection.'
        elif state == 'saved':
            message += 'Start the live demo to see incoming messages update the page.'
        else:
            message += 'Values may be out of date. Check the subscriber logs.'
        metrics = [metric('ACTIVE SESSIONS',number(totals['active_sessions']),'Based on received events',True,id='active-total'),
                   metric('PRICES RECEIVED',f"{totals['priced_stations']} / {totals['stations']}",'Latest saved price · A$/kWh',id='priced-total'),
                   metric('COMPLETED ENERGY',number(totals['completed_energy_kwh'],2),'kWh · received completed sessions',id='energy-total'),
                   metric('COMPLETED SESSIONS',number(totals['completed_sessions']),'From saved session endings',id='completed-total')]
        operators = [{'label':'All operators','value':'all'}] + [{'label':o,'value':o} for o in sorted({s.get('operator_name') for s in snapshot['stations'] if s.get('operator_name')})]
        options = [{'label':f"#{s['ev_id']} · {s.get('display_name') or s.get('station_address')}", 'value':s['ev_id']} for s in snapshot['stations']]
        features = station_feature_collection(subset,selected)
        # A heartbeat alone must not replace marker nodes or disturb a user's click.
        return (labels[state], f'source-badge {state}', message, f'data-notice {state}', metrics,
                f"{len(subset)} of {totals['stations']} DC records shown",
                no_update if features == current_features else features, operators, options)

    @app.callback(Output('station-detail','children'), Output('session-curve','figure'), Output('session-note','children'),
                  Output('current-chart','figure'), Output('history-chart','figure'), Output('popup-holder','children'),
                  Input('snapshot-store','data'), Input('selected-station','data'), Input('session-picker','value'),
                  Input('popup-open','data'))
    def selected_details(envelope, selected, session_id, popup_open=True):
        snapshot = envelope.get('snapshot') or EMPTY
        station = next((s for s in snapshot['stations'] if s['ev_id'] == selected), None)
        if station is None:
            prompt = html.Div([html.Div('↗',className='empty-arrow'),html.H3('Pick a charger'),
                               html.P('Click a marker or choose a station to inspect its received events.')],
                              className='empty-selection')
            return (prompt, empty_figure(), '', empty_figure(), empty_figure(), [])
        sessions = [s for s in snapshot['sessions'] if s['ev_id'] == selected]
        session = next((s for s in sessions if s['session_id'] == session_id), sessions[0] if sessions else None)
        history = [r for r in snapshot['daily_usage'] if r['ev_id'] == selected]
        if session:
            note = (f"{len(session.get('meter_curve', []))} meter readings · Last meter {local_time(session.get('latest_meter_ts'),True)} · "
                    f"{session.get('status')} until a received end event")
        else:
            note = 'No active session. Completed values are preserved in history.'
        popup = dl.Popup(id='station-popup', position=[station['latitude'],station['longitude']],
                         children=popup_contents(station,sessions,session), minWidth=300, maxWidth=410,
                         autoPan=True, autoPanPadding=[15,15], closeOnClick=False, maxHeight=540)
        current = current_state_figure(station)
        current.update_layout(title=None, uirevision=f"current:{selected}")
        for trace in current.data:
            trace.title.font.size = 12
            trace.number.font.size = 34
        if len(current.data) > 1:
            current.data[1].title.text = 'Latest price / kWh'
        historical = history_figure(history) if history else empty_figure('No completed sessions received for this station.')
        historical.update_layout(uirevision=f"history:{selected}")
        return (station_details(station,sessions), curve_figure(session), note,
                style_figure(current,250), style_figure(historical,250), [popup] if popup_open else [])

    @app.callback(Output('charger-map','viewport'), Input('fit-map','n_clicks'),
                  Input('selected-station','data'), State('snapshot-store','data'), prevent_initial_call=True)
    def focus_map(_, selected, envelope):
        if ctx.triggered_id == 'fit-map':
            return dict(center=[-32.2,147.4],zoom=6,transition='flyTo')
        station = next((s for s in (envelope.get('snapshot') or EMPTY)['stations'] if s['ev_id']==selected),None)
        if station is None:
            raise PreventUpdate
        # Offset the view slightly to give the real map popup room above its anchor.
        return dict(center=[station['latitude']+.08,station['longitude']],zoom=12,transition='flyTo')

    @app.server.get('/healthz')
    def health():
        return jsonify(status='ok', component='member4-dashboard', mode=mode)

    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['auto','live','saved'],default='auto')
    parser.add_argument('--snapshot',type=Path,default=ROOT/'runtime/dashboard.json')
    parser.add_argument('--saved-snapshot',type=Path,default=ROOT/'artifacts/dashboard.json')
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8050)
    parser.add_argument('--interval',type=int,default=1000,help='Polling interval in milliseconds (default 1000).')
    parser.add_argument('--stale-after',type=float,default=5)
    args = parser.parse_args()
    if args.interval < 200 or args.stale_after <= 0:
        parser.error('interval must be >= 200 ms and stale-after must be positive')
    create_app(args.mode,args.snapshot,args.saved_snapshot,args.interval,args.stale_after).run(host=args.host,port=args.port,debug=False)
