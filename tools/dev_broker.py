"""Loopback-only Python MQTT broker for testing without Docker (not persistent)."""
import argparse
import asyncio
import logging

from amqtt.broker import Broker


async def serve(port):
    broker = Broker({
        'listeners': {'default': {'type': 'tcp', 'bind': f'127.0.0.1:{port}'}},
        'plugins': {'amqtt.plugins.authentication.AnonymousAuthPlugin': {'allow_anonymous': True}},
    })
    await broker.start()
    try:
        await asyncio.Event().wait()
    finally:
        await broker.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=1883)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    try:
        asyncio.run(serve(args.port))
    except KeyboardInterrupt:
        pass
