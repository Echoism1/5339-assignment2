"""Run the MQTT dashboard demo using a temporary database copy."""
import argparse
from datetime import datetime, timezone
import json
import math
import socket
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


sys.path.insert(0, str(ROOT))
from tools.runtime import Processes, fresh_database, port_ready, wait_for


def port_available(port):
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1',port))
        except OSError:
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8050,help='Dashboard HTTP port.')
    parser.add_argument('--mqtt-port',type=int,default=18883,help='Private loopback MQTT broker port.')
    parser.add_argument('--snapshot',type=Path,default=ROOT/'runtime/dashboard.json')
    parser.add_argument('--publish-delay',type=float,default=8,help='Seconds to open the dashboard before replay starts.')
    parser.add_argument('--exit-after-replay',action='store_true')
    args = parser.parse_args()
    if (not all(1 <= p <= 65535 for p in (args.port,args.mqtt_port))
            or args.port == args.mqtt_port or not all(port_available(p) for p in (args.port,args.mqtt_port))):
        parser.error('Ports must be different and unused. Choose --port and/or --mqtt-port.')
    if not math.isfinite(args.publish_delay) or args.publish_delay < 0:
        parser.error('--publish-delay must be finite and nonnegative')

    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run = ROOT/'runtime'/'demo-runs'/run_id
    run.mkdir(parents=True)
    database = run/'demo.duckdb'
    fresh_database(database)
    group = Processes(run)
    launch, processes = group.launch, group.processes
    def app_ready():
        try:
            with urlopen(f'http://127.0.0.1:{args.port}/healthz',timeout=1) as response:
                return response.status == 200
        except OSError:
            return False
    def snapshot_count():
        try:
            return json.loads(args.snapshot.read_text(encoding='utf-8'))['coverage']['received_events']
        except (OSError,ValueError,KeyError):
            return -1

    metadata = dict(run_id=run_id, run_directory=str(run), database=str(database),
                    snapshot=str(args.snapshot.resolve()), dashboard_url=f'http://127.0.0.1:{args.port}',
                    mqtt_port=args.mqtt_port, expected_events=2452)
    (ROOT/'runtime'/'latest_demo.json').write_text(json.dumps(metadata,indent=2)+'\n', encoding='utf-8')
    try:
        launch('broker',['tools/dev_broker.py','--port',str(args.mqtt_port)])
        wait_for(lambda: port_ready(args.mqtt_port),15,'MQTT broker',processes)
        launch('subscriber',['subscriber.py','listen','--db',str(database),'--port',str(args.mqtt_port),
               '--client-id','member4-demo-'+run_id,'--snapshot',str(args.snapshot),
               '--snapshot-interval','1','--summary',str(run/'subscriber_summary.json')], stop_file=run/'subscriber.stop')
        wait_for(lambda: 'READY' in (run/'subscriber.log').read_text(encoding='utf-8'),30,'subscriber READY',processes)
        wait_for(lambda: snapshot_count()==0,10,'initial empty snapshot',processes)
        launch('dashboard',['app.py','--mode','live','--snapshot',str(args.snapshot),'--port',str(args.port)])
        wait_for(app_ready,20,'dashboard HTTP server',processes)
        print(f"READY {metadata['dashboard_url']}\nRun directory: {run}\nReplay starts in {args.publish_delay:g} seconds.",flush=True)
        deadline = time.monotonic()+args.publish_delay
        while time.monotonic()<deadline:
            time.sleep(.1)
        publisher = launch('publisher',['publisher.py','data/window.jsonl','--port',str(args.mqtt_port),
                           '--topic','comp5339/TUT19-AssignmentGroup-08/events'])
        began = time.monotonic()
        while publisher.poll() is None:
            if any(p.poll() is not None for p in processes[:-1]):
                raise RuntimeError('A service exited during replay; inspect logs.')
            if time.monotonic()-began > 650:
                raise TimeoutError('Replay exceeded 650 seconds.')
            time.sleep(.2)
        if publisher.returncode != 0:
            raise RuntimeError('Publisher failed; inspect publisher.log.')
        wait_for(lambda: snapshot_count()==2452,30,'all 2,452 committed events',processes[:-1])
        print('REPLAY COMPLETE: 2,452 events received. Dashboard remains available. Ctrl+C stops this demo.',flush=True)
        if not args.exit_after_replay:
            while True:
                if any(p.poll() is not None for p in processes[:-1]):
                    raise RuntimeError('A demo service exited; inspect logs.')
                time.sleep(.5)
        return 0
    except KeyboardInterrupt:
        print('Stopping demo services.',flush=True)
        return 0
    finally:
        group.close()


if __name__ == '__main__':
    raise SystemExit(main())
