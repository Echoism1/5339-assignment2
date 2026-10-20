"""Publish JSONL in order, waiting for MQTT acknowledgement and a 0.1s delay."""
import argparse
import json
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt


def publish(path, host, port, topic):
    ready = threading.Event()
    status = []
    last, previous, min_gap = None, None, None
    count = 0
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, reconnect_on_failure=False)
    client.connect_timeout = 10

    def on_connect(client, userdata, flags, reason, properties):
        status.append(reason)
        ready.set()

    client.on_connect = on_connect
    try:
        client.connect(host, port)
        client.loop_start()
        if not ready.wait(10):
            raise TimeoutError("Broker connection acknowledgement timed out")
        if status[0].is_failure:
            raise ConnectionError(f"Broker rejected connection: {status[0]}")
        with path.open(encoding="utf-8") as f:
            for line in f:
                event_id = json.loads(line)["event_id"]
                now = time.monotonic()
                if previous is not None:
                    gap = now - previous
                    min_gap = gap if min_gap is None else min(min_gap, gap)
                info = client.publish(topic, line.strip(), qos=1, retain=False)
                previous = now
                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    raise ConnectionError(mqtt.error_string(info.rc))
                info.wait_for_publish(timeout=10)
                if not info.is_published():
                    raise TimeoutError(f"Publish acknowledgement timed out: {event_id}")
                last = event_id
                count += 1
                if count % 1000 == 0:
                    print(f"Confirmed {count}; last_event_id={last}", flush=True)
                time.sleep(0.1)
    except (OSError, ValueError, KeyError, RuntimeError, KeyboardInterrupt) as error:
        print(f"Stopped: {str(error) or type(error).__name__}; confirmed={count}; last_event_id={last}", file=sys.stderr)
        return 130 if isinstance(error, KeyboardInterrupt) else 1
    finally:
        client.disconnect()
        client.loop_stop()
    print(json.dumps(dict(confirmed=count, last_event_id=last, min_interval_seconds=min_gap)))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", type=Path)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--topic", required=True)
    args = parser.parse_args()
    if not args.topic or any(c in args.topic for c in "+#\0"):
        parser.error("topic must be a nonempty MQTT publish topic without wildcards")
    sys.exit(publish(args.events, args.host, args.port, args.topic))
