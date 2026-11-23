"""Fresh real-MQTT replay plus concurrent frontend snapshot reads (about 5 minutes)."""
import argparse
import json
import socket
import sys
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import duckdb
from tools.verify_ingestion import verify
from tools.verify_a1_preserved import A2_TABLES, compare_tables
from tools.runtime import Processes, fresh_database, port_ready, wait_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'verification/task3')
    out = parser.parse_args().output_dir
    out.mkdir(parents=True,exist_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    with tempfile.TemporaryDirectory() as directory:
        tmp=Path(directory); db=tmp/'fresh.duckdb'; snap=tmp/'dashboard.json'
        fresh_database(db)
        group = Processes(out)
        launch = group.launch
        reads=0; observed=set(); began=time.monotonic()
        curve_lengths = set()
        active306 = ended306 = False
        try:
            broker=launch('broker',['tools/dev_broker.py','--port',str(port)])
            wait_for(lambda: port_ready(port), 15, 'broker', group.processes)
            sub=launch('subscriber',['subscriber.py','listen','--port',str(port),'--db',str(db),
                 '--client-id','member3-verification','--snapshot',str(snap),
                 '--snapshot-interval','0.25','--summary',str(out/'subscriber_summary.json')], stop_file=tmp/'subscriber.stop')
            wait_for(lambda: 'READY' in (out/'subscriber.log').read_text(encoding='utf-8'),
                     30, 'subscriber READY', group.processes)
            pub=launch('publisher',['publisher.py','data/window.jsonl','--port',str(port),
                 '--topic','comp5339/TUT19-AssignmentGroup-08/events'])
            while pub.poll() is None:
                if sub.poll() is not None: raise RuntimeError('subscriber exited')
                if time.monotonic()-began>600: raise TimeoutError('replay')
                if snap.exists():
                    data=json.loads(snap.read_text(encoding='utf-8'))
                    reads+=1; observed.add(data['coverage']['received_events'])
                    assert len(data['stations'])==433
                    station306 = next(s for s in data['stations'] if s['ev_id'] == 306)
                    active306 |= station306['active_sessions'] > 0
                    ended306 |= (active306 and station306['active_sessions'] == 0
                                 and station306['completed_sessions'] > 0)
                    for session in data['sessions']:
                        if session['ev_id'] == 524:
                            curve_lengths.add(len(session['meter_curve']))
                time.sleep(.08)
            assert pub.returncode==0
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                data=json.loads(snap.read_text(encoding='utf-8'))
                if data['coverage']['received_events']==2452: break
                time.sleep(.1)
            assert data['coverage']['received_events']==2452
            assert active306 and ended306, 'Station 306 did not complete its observed session'
            assert len(curve_lengths) > 1, 'Station 524 curve did not grow during replay'
            assert len(next(s for s in data['sessions'] if s['ev_id'] == 524)['meter_curve']) == 81
            assert group.stop(sub) == 0
            result=verify(db,ROOT/'data/window.jsonl')
            assert result['passed'],result
            with duckdb.connect(str(db),read_only=True) as conn:
                state=dict(conn.execute('SELECT status,count(*) FROM core.charging_session GROUP BY status').fetchall())
                assert state=={'active':9,'completed':62},state
                assert conn.execute('SELECT count(*) FROM core.meter_reading').fetchone()[0]==1886
                preservation = {r['table']: r['differences'] for r in
                                compare_tables(conn, ROOT/'database/assignment2.duckdb', A2_TABLES)}
                assert preservation and not any(preservation.values())
            result.update(session_states=state,snapshot_reads=reads,distinct_live_revisions=len(observed),
                          station306_completed=ended306,station524_curve_lengths=sorted(curve_lengths),
                          original_table_differences=preservation,elapsed_seconds=round(time.monotonic()-began,2))
            assert len(observed)>10
            (out/'mqtt_integration.json').write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps(result,indent=2))
        finally:
            group.close()


if __name__=='__main__': main()
